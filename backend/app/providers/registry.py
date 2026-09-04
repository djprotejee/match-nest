from __future__ import annotations

import json
import hashlib
import logging
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError

from dotenv import load_dotenv

from .base import EventProvider
from .api_football import ApiFootballProvider
from .espn_football import ESPN_FOOTBALL_COMPETITIONS, ESPN_FOOTBALL_TEAMS, EspnFootballProvider
from .f1_jolpica import JolpicaF1Provider
from .f4_calendar import F4CalendarProvider
from .football_data import FootballDataProvider, football_competition_entities
from .thesportsdb import TheSportsDBFootballProvider
from .pandascore import PandaScoreCS2Provider
from ..details import normalize_event_details
from ..models import EntityKind, Event, EventStatus, F1Session, FollowLevel, Sport, UserPreferences
from ..seed import DEFAULT_PREFERENCES, demo_events
from ..storage import (
    delete_stale_events_for_source,
    event_details_cache_state,
    get_event,
    list_entity_records,
    list_events,
    mark_provider_fetch,
    provider_bindings_for,
    provider_fetch_state,
    upsert_event_details_cache,
    upsert_events,
)

INACTIVE_LEVELS = {FollowLevel.HIDDEN.value, FollowLevel.MUTED.value, FollowLevel.EXPLORE.value}


@dataclass
class ProviderResult:
    name: str
    configured: bool
    count: int
    error: str | None = None
    refreshing: bool = False


_CACHE_TTL = timedelta(minutes=1)
PROVIDER_CACHE_SCHEMA_VERSION = "v5"
_CACHE: dict[str, tuple[datetime, list[ProviderResult], list[Event]]] = {}
# Independent serial queues keep slow history imports from blocking other sports.
_PROVIDER_EXECUTORS: dict[str, ThreadPoolExecutor] = {}


def provider_executor(name: str) -> ThreadPoolExecutor:
    # Called while _IN_FLIGHT_LOCK is held.
    if name not in _PROVIDER_EXECUTORS:
        _PROVIDER_EXECUTORS[name] = ThreadPoolExecutor(max_workers=1, thread_name_prefix=name)
    return _PROVIDER_EXECUTORS[name]
_IN_FLIGHT_LOCK = threading.Lock()
_IN_FLIGHT_REFRESHES: dict[str, Future] = {}

PROVIDER_REFRESH_TTL = {
    "JolpicaF1Provider": timedelta(hours=24),
    "F4CalendarProvider": timedelta(days=7),
    "FootballDataProvider": timedelta(hours=12),
    "EspnFootballProvider": timedelta(hours=6),
    "ApiFootballProvider": timedelta(hours=12),
    "TheSportsDBFootballProvider": timedelta(hours=6),
    "PandaScoreCS2Provider": timedelta(minutes=3),
}


def load_environment() -> None:
    repo_root = Path(__file__).resolve().parents[3]

    # Keep one canonical environment file at the repository root. Multiple .env
    # locations make local debugging ambiguous, especially when the app is moved.
    load_dotenv(repo_root / ".env", override=False)


def configured_providers(preferences: UserPreferences | None = None) -> list[EventProvider]:
    load_environment()
    active_preferences = preferences or DEFAULT_PREFERENCES
    return [
        PandaScoreCS2Provider(),
        JolpicaF1Provider(),
        F4CalendarProvider(),
        EspnFootballProvider(
            team_ids=followed_espn_team_ids(active_preferences),
            competition_entities=followed_espn_competitions(active_preferences),
        ),
        FootballDataProvider(
            competition_entities=followed_football_competitions(active_preferences),
            team_queries=followed_football_team_queries(active_preferences),
        ),
        ApiFootballProvider(
            team_queries=followed_football_team_queries(active_preferences),
            team_entities=followed_api_football_team_ids(active_preferences),
        ),
        TheSportsDBFootballProvider(team_ids=followed_thesportsdb_team_ids(active_preferences)),
    ]


def provider_results(
    start: datetime | None = None,
    end: datetime | None = None,
    preferences: UserPreferences | None = None,
) -> tuple[list[ProviderResult], list[Event]]:
    cache_key = range_cache_key(start, end, preferences)
    cached = _CACHE.get(cache_key)
    if cached:
        cached_at, cached_results, cached_events = cached
        ttl = timedelta(seconds=2) if any(result.refreshing for result in cached_results) else timedelta(seconds=10) if any(result.error for result in cached_results) else _CACHE_TTL
        if datetime.now() - cached_at < ttl:
            return cached_results, cached_events

    events: list[Event] = []
    results: list[ProviderResult] = []
    now = datetime.now(timezone.utc)
    db_events = list_events(start, end)

    for provider in configured_providers(preferences):
        name = provider.__class__.__name__
        configured = provider_is_configured(provider)
        if not configured:
            results.append(ProviderResult(name=name, configured=False, count=0))
            continue
        cached_count = len([event for event in db_events if provider_matches_event(name, event)])
        schedule_key = provider_schedule_key(provider, start, end)
        if not provider_should_refresh(name, schedule_key, start, end):
            results.append(ProviderResult(name=name, configured=True, count=cached_count))
            continue

        if name == "F4CalendarProvider":
            provider_events = refresh_provider(provider, name, schedule_key, start, end)
            results.append(ProviderResult(name=name, configured=True, count=len(provider_events)))
            continue

        refresh_key = f"{name}:{schedule_key}"
        refresh_provider_async(refresh_key, provider, name, schedule_key, start, end)
        results.append(
            ProviderResult(
                name=name,
                configured=True,
                count=cached_count,
                refreshing=True,
                error=None if cached_count else "Refresh is running in the background.",
            )
        )

    events = dedupe_cross_source_events(list_events(start, end))
    if events:
        write_events_cache(cache_key, events)
    else:
        stale_events = read_events_cache(cache_key)
        if stale_events:
            events = stale_events
            results.append(
                ProviderResult(
                    name="StaleDiskCache",
                    configured=True,
                    count=len(stale_events),
                    error="Serving last successful cache while providers refresh.",
                )
            )

    if len(_CACHE) >= 64:
        _CACHE.pop(next(iter(_CACHE), cache_key), None)
    _CACHE[cache_key] = (datetime.now(), results, events)
    return results, events


def refresh_provider_async(
    refresh_key: str,
    provider: EventProvider,
    provider_name: str,
    cache_key: str,
    start: datetime | None,
    end: datetime | None,
) -> Future:
    with _IN_FLIGHT_LOCK:
        existing = _IN_FLIGHT_REFRESHES.get(refresh_key)
        if existing and not existing.done():
            return existing
        future = provider_executor(provider_name).submit(refresh_provider_if_needed, provider, provider_name, cache_key, start, end)
        _IN_FLIGHT_REFRESHES[refresh_key] = future
    # A completed future invokes its callback immediately. Register outside the
    # lock so a fast/empty provider cannot deadlock every subsequent refresh.
    future.add_done_callback(lambda _: clear_in_flight_refresh(refresh_key))
    return future


def refresh_is_running(start: datetime, end: datetime, preferences: UserPreferences) -> bool:
    key = range_cache_key(start, end, preferences)
    cached = _CACHE.get(key)
    if cached and any(result.refreshing for result in cached[1]):
        return True
    keys = {f"{type(provider).__name__}:{provider_schedule_key(provider, start, end)}" for provider in configured_providers(preferences)}
    with _IN_FLIGHT_LOCK:
        return any(key in keys and not future.done() for key, future in _IN_FLIGHT_REFRESHES.items())


def clear_in_flight_refresh(refresh_key: str) -> None:
    with _IN_FLIGHT_LOCK:
        future = _IN_FLIGHT_REFRESHES.get(refresh_key)
        if future and future.done():
            _IN_FLIGHT_REFRESHES.pop(refresh_key, None)


def refresh_provider_if_needed(provider, provider_name, cache_key, start, end):
    # Another user/view may have completed this schedule while the job queued.
    if not provider_should_refresh(provider_name, cache_key, start, end):
        return []
    return refresh_provider(provider, provider_name, cache_key, start, end)


def refresh_provider(
    provider: EventProvider,
    provider_name: str,
    cache_key: str,
    start: datetime | None,
    end: datetime | None,
) -> list[Event]:
    start, end = provider_date_range(start, end)
    try:
        provider_events = provider.fetch(start=start, end=end)
        upsert_events(provider_events)
        if provider_allows_stale_event_deletion(provider_name):
            delete_stale_events_for_source(provider.source, start, end, [event.id for event in provider_events])
        errors = getattr(provider, "refresh_errors", [])
        if errors:
            logging.getLogger("matchnest").warning("Provider partial refresh: %s %s %s", provider_name, cache_key, "; ".join(errors))
        mark_provider_fetch(provider_name, cache_key, "error" if errors else "ok", "; ".join(errors) if errors else None)
        _CACHE.clear()
        return provider_events
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        mark_provider_fetch(provider_name, cache_key, "error", f"HTTP {exc.code}: {body}")
        _CACHE.clear()
        raise
    except Exception as exc:
        logging.getLogger("matchnest").warning("Provider refresh failed: %s %s %s", provider_name, cache_key, exc)
        mark_provider_fetch(provider_name, cache_key, "error", str(exc))
        _CACHE.clear()
        raise


def provider_allows_stale_event_deletion(provider_name: str) -> bool:
    return provider_name in {"F4CalendarProvider"}


def fetch_events(
    start: datetime | None = None,
    end: datetime | None = None,
    preferences: UserPreferences | None = None,
) -> list[Event]:
    # Real providers are the default. Demo data is only a local development
    # fallback and must be explicitly enabled through MATCHNEST_ALLOW_DEMO_EVENTS.
    _, events = provider_results(start, end, preferences)
    if not events and os.getenv("MATCHNEST_ALLOW_DEMO_EVENTS") == "1":
        return demo_events()
    return events


_DETAIL_LOCKS = [threading.Lock() for _ in range(32)]


def fetch_event_details(event_id: str) -> dict | None:
    with _DETAIL_LOCKS[hash(event_id) % len(_DETAIL_LOCKS)]:
        return _fetch_event_details(event_id)


def _fetch_event_details(event_id: str) -> dict | None:
    load_environment()
    event = get_event(event_id)
    cached_state = event_details_cache_state(event_id)
    cached = cached_state.details if cached_state else None
    if cached_state and event_details_cache_is_fresh(cached_state.fetched_at, event) and event_details_cache_is_usable(event_id, cached_state.details):
        return normalize_event_details(cached_state.details)
    try:
        details = None
        if event_id.startswith("f1-"):
            details = JolpicaF1Provider().details(event_id)
        elif event_id.startswith("f4-"):
            details = F4CalendarProvider().details(event_id)
        elif event_id.startswith("cs2-"):
            details = PandaScoreCS2Provider().details(event_id, event)
        elif event_id.startswith("football-espn-"):
            details = ApiFootballProvider().details_for_event(event_id, event) or EspnFootballProvider().details(event_id, event)
        elif event_id.startswith("football-apifootball-"):
            details = ApiFootballProvider().details(event_id)
        details = normalize_event_details(details)
        if details and details.get("source") != "matchnest":
            upsert_event_details_cache(event_id, str(details.get("source") or "unknown"), details)
        return details or cached
    except Exception as exc:
        if cached:
            cached_copy = normalize_event_details(json.loads(json.dumps(cached))) or json.loads(json.dumps(cached))
            cached_copy["summary"] = f"{cached_copy.get('summary', 'Cached details')} Cached because the upstream provider is temporarily unavailable."
            cached_copy.setdefault("facts", []).append({"label": "Cache", "value": f"Provider refresh failed: {detail_error_message(exc)}"})
            return cached_copy
        return normalize_event_details({
            "event_id": event_id,
            "sport": "formula" if event_id.startswith("f1-") else "unknown",
            "source": "matchnest",
            "summary": detail_error_summary(event_id, exc),
            "facts": [{"label": "Provider message", "value": detail_error_message(exc)}],
            "sections": [],
        })


def event_details_cache_is_fresh(fetched_at: datetime, event: Event | None) -> bool:
    ttl = event_details_cache_ttl(event)
    return datetime.now(timezone.utc) - fetched_at < ttl


def event_details_cache_is_usable(event_id: str, details: dict) -> bool:
    if event_id.startswith("f1-") and event_id.endswith("-race"):
        return f1_race_details_are_complete(details)
    return True


def f1_race_details_are_complete(details: dict) -> bool:
    for section in details.get("sections") or []:
        title = str(section.get("title") or "").strip().lower()
        if title != "race classification":
            continue
        columns = {str(column).strip().lower() for column in section.get("columns") or []}
        rows = section.get("rows") or []
        required_columns = {"pos", "drv", "driver", "team", "laps", "time / status"}
        return required_columns.issubset(columns) and len(rows) >= 10
    return False


def event_details_cache_ttl(event: Event | None) -> timedelta:
    if event is None:
        return timedelta(minutes=5)
    if event.status == EventStatus.PAST:
        return timedelta(days=14)
    if event.status in {EventStatus.LIVE, EventStatus.DELAYED}:
        return timedelta(seconds=45)
    if event.status == EventStatus.TBD:
        return timedelta(minutes=2)
    return timedelta(minutes=10)


def detail_error_summary(event_id: str, exc: Exception) -> str:
    message = str(exc)
    if event_id.startswith("f1-") and ("403" in message or "Forbidden" in message or "404" in message):
        return (
            "Session classification is not available yet, or the upstream timing provider "
            "has not published it. Try again later."
        )
    if event_id.startswith("f1-") and ("DataNotLoadedError" in message or "not been loaded" in message):
        return "Session timing data has not been published by the upstream provider yet. Try again later."
    return "Details are temporarily unavailable. Try again later."


def detail_error_message(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        return exc.__class__.__name__
    if "403" in message or "Forbidden" in message:
        return "Upstream provider returned 403 Forbidden."
    if "404" in message:
        return "Upstream provider has not published this session payload yet."
    if "DataNotLoadedError" in message or "not been loaded" in message:
        return "Upstream timing data is not loaded yet."
    return message


def provider_is_configured(provider: EventProvider) -> bool:
    if isinstance(provider, FootballDataProvider):
        return bool(provider.token and (provider.team_queries or provider.team_entities or provider.competition_entities))
    if isinstance(provider, EspnFootballProvider):
        return bool(provider.team_ids or provider.competition_entities)
    if isinstance(provider, ApiFootballProvider):
        return bool(provider.token)
    if isinstance(provider, TheSportsDBFootballProvider):
        return bool(provider.team_ids)
    if isinstance(provider, PandaScoreCS2Provider):
        return bool(provider.token)
    return True


def provider_should_refresh(provider_name: str, cache_key: str, start: datetime | None = None, end: datetime | None = None) -> bool:
    fetch_state = provider_fetch_state(provider_name, cache_key)
    if fetch_state is None:
        return True
    if fetch_state.status != "ok":
        return datetime.now(timezone.utc) - fetch_state.fetched_at >= timedelta(minutes=2)
    ttl = provider_refresh_ttl(provider_name, start, end)
    return datetime.now(timezone.utc) - fetch_state.fetched_at >= ttl


def provider_refresh_ttl(provider_name: str, start: datetime | None = None, end: datetime | None = None) -> timedelta:
    # Minute cron jobs only need fresh data for live/recent windows. Broader
    # schedule windows stay cached so hosted databases and free APIs are not
    # burned by background maintenance.
    if end is not None and end < datetime.now(timezone.utc) - timedelta(days=2):
        return timedelta(days=7)
    if range_is_hot_window(start, end):
        if provider_name == "PandaScoreCS2Provider":
            return timedelta(minutes=2)
        if provider_name in {"EspnFootballProvider", "JolpicaF1Provider"}:
            return timedelta(minutes=5)
    if provider_name == "PandaScoreCS2Provider":
        return timedelta(minutes=20)
    return PROVIDER_REFRESH_TTL.get(provider_name, timedelta(hours=1))


def range_is_hot_window(start: datetime | None, end: datetime | None) -> bool:
    if start is None or end is None:
        return False
    now = datetime.now(timezone.utc)
    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    return (
        end_utc - start_utc <= timedelta(days=2)
        and start_utc <= now + timedelta(hours=18)
        and end_utc >= now - timedelta(hours=6)
    )


def range_is_near_now(start: datetime | None, end: datetime | None) -> bool:
    if start is None or end is None:
        return True
    now = datetime.now(timezone.utc)
    return start.astimezone(timezone.utc) <= now + timedelta(days=2) and end.astimezone(timezone.utc) >= now - timedelta(days=1)


def provider_matches_event(provider_name: str, event: Event) -> bool:
    sources = {
        "JolpicaF1Provider": "jolpica", "F4CalendarProvider": "f4-calendar",
        "FootballDataProvider": "football-data", "EspnFootballProvider": "espn",
        "ApiFootballProvider": "api-football", "TheSportsDBFootballProvider": "thesportsdb",
        "PandaScoreCS2Provider": "pandascore",
    }
    return event.source == sources.get(provider_name)


def provider_date_range(start: datetime | None, end: datetime | None):
    # Stable UTC day bounds share payload URLs between users and cron ticks.
    # Expand, never truncate: response filtering still uses the original range.
    if start is not None:
        start = start.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if end is not None:
        utc_end = end.astimezone(timezone.utc)
        end = utc_end.replace(hour=0, minute=0, second=0, microsecond=0)
        if utc_end > end:
            end += timedelta(days=1)
    return start, end


def provider_schedule_key(provider: EventProvider, start: datetime | None, end: datetime | None) -> str:
    # Follows and spoiler settings belong to the response filter. Only inputs
    # that change the upstream schedule belong in the shared refresh key.
    start, end = provider_date_range(start, end)
    inputs = {key: getattr(provider, key) for key in (
        "team_ids", "team_entities", "team_queries", "competition_entities", "token", "api_key", "url", "base_url"
    ) if hasattr(provider, key)}
    serialized = json.dumps(inputs, sort_keys=True, default=lambda value: sorted(value))
    signature = hashlib.sha256(serialized.encode()).hexdigest()[:24]
    start_key = start.isoformat() if start else "default"
    end_key = end.isoformat() if end else "default"
    return f"schedule-v1:{start_key}:{end_key}:{signature}"


def range_cache_key(start: datetime | None, end: datetime | None, preferences: UserPreferences | None = None) -> str:
    start_key = start.isoformat() if start else "default"
    end_key = end.isoformat() if end else "default"
    follow_key = "default"
    if preferences:
        followed = sorted(
            entity_id
            for entity_id, follow in preferences.follows.items()
            if is_active_follow_level(follow.level)
        )
        follow_key = "|".join(followed) or "none"
    return f"{PROVIDER_CACHE_SCHEMA_VERSION}:{start_key}:{end_key}:{follow_key}"


def followed_football_team_queries(preferences: UserPreferences) -> dict[str, list[str]]:
    queries: dict[str, list[str]] = {}
    entities = list_entity_records()
    for entity_id, follow in preferences.follows.items():
        record = entities.get(entity_id)
        if not record or not is_active_follow_level(follow.level):
            continue
        entity = record.entity
        if entity.sport == Sport.FOOTBALL and entity.kind == EntityKind.TEAM:
            queries[entity_id] = list(dict.fromkeys([*team_queries_for_entity_name(entity.name), *record.aliases]))
    return queries



def followed_thesportsdb_team_ids(preferences: UserPreferences) -> dict[str, int]:
    # ESPN covers the current free future-fixture fallback use case without a
    # token. Keep TheSportsDB available in code, but avoid duplicate fixtures in
    # the normal provider cycle.
    return {}


def followed_espn_team_ids(preferences: UserPreferences) -> dict[str, set[str]]:
    team_ids: dict[str, set[str]] = {}
    entities = list_entity_records()
    espn_team_bindings = provider_bindings_for("espn", "team_id")
    for entity_id, follow in preferences.follows.items():
        record = entities.get(entity_id)
        if not record or not is_active_follow_level(follow.level):
            continue
        entity = record.entity
        if entity.sport == Sport.FOOTBALL and entity.kind == EntityKind.TEAM:
            values = espn_team_bindings.get(entity_id) or list(ESPN_FOOTBALL_TEAMS.get(entity_id, {}).get("ids", []))
            if values:
                team_ids[entity_id] = set(values)
    return team_ids


def followed_api_football_team_ids(preferences: UserPreferences) -> dict[str, int]:
    team_ids: dict[str, int] = {}
    entities = list_entity_records()
    api_bindings = provider_bindings_for("api-football", "team_id")
    for entity_id, follow in preferences.follows.items():
        record = entities.get(entity_id)
        if not record or not is_active_follow_level(follow.level):
            continue
        if record.entity.sport == Sport.FOOTBALL and record.entity.kind == EntityKind.TEAM:
            values = api_bindings.get(entity_id) or []
            if values and values[0].isdigit():
                team_ids[entity_id] = int(values[0])
    return team_ids


def followed_espn_competitions(preferences: UserPreferences) -> dict[str, str]:
    competitions: dict[str, str] = {}
    entities = list_entity_records()
    espn_competition_bindings = provider_bindings_for("espn", "league_slug")
    for entity_id, follow in preferences.follows.items():
        record = entities.get(entity_id)
        if not record or not is_active_follow_level(follow.level):
            continue
        entity = record.entity
        if entity.sport == Sport.FOOTBALL and entity.kind == EntityKind.COMPETITION:
            values = espn_competition_bindings.get(entity_id) or ([ESPN_FOOTBALL_COMPETITIONS[entity_id]] if entity_id in ESPN_FOOTBALL_COMPETITIONS else [])
            if values:
                competitions[entity_id] = values[0]
    return competitions

def followed_football_competitions(preferences: UserPreferences) -> dict[str, str]:
    supported = football_competition_entities()
    competitions: dict[str, str] = {}
    entities = list_entity_records()
    football_data_bindings = provider_bindings_for("football-data", "competition_code")
    for entity_id, follow in preferences.follows.items():
        record = entities.get(entity_id)
        if not record or not is_active_follow_level(follow.level):
            continue
        entity = record.entity
        if entity.sport == Sport.FOOTBALL and entity.kind == EntityKind.COMPETITION:
            values = football_data_bindings.get(entity_id) or ([supported[entity_id]] if entity_id in supported else [])
            if values:
                competitions[entity_id] = values[0]
    return competitions


def team_queries_for_entity_name(name: str) -> list[str]:
    queries = [name]
    if name.endswith(" NT"):
        queries.append(name.removesuffix(" NT"))
    if name.startswith("FC "):
        queries.append(name.removeprefix("FC "))
    return queries


def follow_level_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def is_active_follow_level(value) -> bool:
    return follow_level_value(value) not in INACTIVE_LEVELS


def events_cache_path(cache_key: str) -> Path:
    safe_key = cache_key.replace(":", "_").replace("|", "_")
    return Path(__file__).resolve().parents[2] / ".cache" / f"provider-events-{safe_key}.json"


def write_events_cache(cache_key: str, events: list[Event]) -> None:
    path = events_cache_path(cache_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [event_to_cache(event) for event in events]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def read_events_cache(cache_key: str) -> list[Event]:
    path = events_cache_path(cache_key)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [event_from_cache(item) for item in payload if isinstance(item, dict)]


def event_to_cache(event: Event) -> dict:
    payload = asdict(event)
    payload["sport"] = event.sport.value
    payload["status"] = event.status.value
    payload["session_type"] = event.session_type.value if event.session_type else None
    payload["starts_at"] = event.starts_at.isoformat() if event.starts_at else None
    return payload


def event_from_cache(payload: dict) -> Event:
    starts_at = payload.get("starts_at")
    return Event(
        id=payload["id"],
        title=payload["title"],
        sport=Sport(payload["sport"]),
        starts_at=datetime.fromisoformat(starts_at).astimezone(timezone.utc) if starts_at else None,
        status=EventStatus(payload["status"]),
        entity_ids=list(payload.get("entity_ids", [])),
        source=payload["source"],
        competition=payload.get("competition"),
        session_type=F1Session(payload["session_type"]) if payload.get("session_type") else None,
        result_summary=payload.get("result_summary"),
        importance=int(payload.get("importance", 50)),
    )


def dedupe_cross_source_events(events: list[Event]) -> list[Event]:
    source_priority = {
        "api-football": 50,
        "espn": 45,
        "football-data": 40,
        "thesportsdb": 30,
        "pandascore": 50,
        "jolpica": 50,
        "f4-calendar": 45,
    }
    by_key: dict[tuple[str, str, str], Event] = {}
    for event in events:
        key = cross_source_event_key(event)
        existing = by_key.get(key)
        if existing is None or source_priority.get(event.source, 0) > source_priority.get(existing.source, 0):
            by_key[key] = event
    return sorted(by_key.values(), key=lambda event: (event.starts_at or datetime.max.replace(tzinfo=timezone.utc), event.title))


def cross_source_event_key(event: Event) -> tuple[str, str, str]:
    starts_at = event.starts_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M") if event.starts_at else "tbd"
    title = " ".join(event.title.lower().replace("@", " vs ").split())
    return (event.sport.value, starts_at, title)
