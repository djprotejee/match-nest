from __future__ import annotations

import json
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
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
from ..models import EntityKind, Event, EventStatus, F1Session, FollowLevel, Sport, UserPreferences
from ..seed import DEFAULT_PREFERENCES, demo_events
from ..storage import get_event, list_entity_records, list_events, mark_provider_fetch, provider_bindings_for, provider_fetch_state, upsert_events

INACTIVE_LEVELS = {FollowLevel.HIDDEN.value, FollowLevel.MUTED.value, FollowLevel.EXPLORE.value}


@dataclass
class ProviderResult:
    name: str
    configured: bool
    count: int
    error: str | None = None


_CACHE_TTL = timedelta(minutes=1)
_CACHE: dict[str, tuple[datetime, list[ProviderResult], list[Event]]] = {}
PROVIDER_SYNC_TIMEOUT_SECONDS = 0.25
_PROVIDER_EXECUTOR = ThreadPoolExecutor(max_workers=4)
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
        JolpicaF1Provider(),
        F4CalendarProvider(),
        FootballDataProvider(
            competition_entities=followed_football_competitions(active_preferences),
            team_queries=followed_football_team_queries(active_preferences),
        ),
        EspnFootballProvider(
            team_ids=followed_espn_team_ids(active_preferences),
            competition_entities=followed_espn_competitions(active_preferences),
        ),
        ApiFootballProvider(
            team_queries=followed_football_team_queries(active_preferences),
            team_entities=followed_api_football_team_ids(active_preferences),
        ),
        TheSportsDBFootballProvider(team_ids=followed_thesportsdb_team_ids(active_preferences)),
        PandaScoreCS2Provider(),
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
        has_cached_error = any(result.error for result in cached_results)
        if not has_cached_error and datetime.now() - cached_at < _CACHE_TTL:
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
        if name != "PandaScoreCS2Provider" and end is not None and end.astimezone(timezone.utc) <= now and cached_count:
            results.append(ProviderResult(name=name, configured=True, count=cached_count))
            continue
        if not provider_should_refresh(name, cache_key):
            results.append(ProviderResult(name=name, configured=True, count=cached_count))
            continue

        refresh_key = f"{name}:{cache_key}"
        future = refresh_provider_async(refresh_key, provider, name, cache_key, start, end)
        if cached_count > 0:
            results.append(ProviderResult(name=name, configured=True, count=cached_count))
            continue
        try:
            provider_events = future.result(timeout=PROVIDER_SYNC_TIMEOUT_SECONDS)
            results.append(ProviderResult(name=name, configured=True, count=len(provider_events)))
        except TimeoutError:
            results.append(
                ProviderResult(
                    name=name,
                    configured=True,
                    count=cached_count,
                    error=f"Refresh is still running in the background after {PROVIDER_SYNC_TIMEOUT_SECONDS}s.",
                )
            )
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            error = f"HTTP {exc.code}: {body}"
            mark_provider_fetch(name, cache_key, "error", error)
            results.append(ProviderResult(name=name, configured=True, count=cached_count, error=error))
        except Exception as exc:
            error = str(exc)
            mark_provider_fetch(name, cache_key, "error", error)
            results.append(ProviderResult(name=name, configured=True, count=cached_count, error=error))

    events = dedupe_cross_source_events(list_events(start, end))
    if events:
        write_events_cache(cache_key, events)
    elif any(result.error for result in results):
        stale_events = read_events_cache(cache_key)
        if stale_events:
            events = stale_events
            results.append(
                ProviderResult(
                    name="StaleDiskCache",
                    configured=True,
                    count=len(stale_events),
                    error="Serving last successful cache because a provider request failed.",
                )
            )

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
        future = _PROVIDER_EXECUTOR.submit(refresh_provider, provider, provider_name, cache_key, start, end)
        _IN_FLIGHT_REFRESHES[refresh_key] = future
        future.add_done_callback(lambda _: clear_in_flight_refresh(refresh_key))
        return future


def clear_in_flight_refresh(refresh_key: str) -> None:
    with _IN_FLIGHT_LOCK:
        future = _IN_FLIGHT_REFRESHES.get(refresh_key)
        if future and future.done():
            _IN_FLIGHT_REFRESHES.pop(refresh_key, None)


def refresh_provider(
    provider: EventProvider,
    provider_name: str,
    cache_key: str,
    start: datetime | None,
    end: datetime | None,
) -> list[Event]:
    try:
        provider_events = provider.fetch(start=start, end=end)
        upsert_events(provider_events)
        mark_provider_fetch(provider_name, cache_key, "ok")
        return provider_events
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        mark_provider_fetch(provider_name, cache_key, "error", f"HTTP {exc.code}: {body}")
        raise
    except Exception as exc:
        mark_provider_fetch(provider_name, cache_key, "error", str(exc))
        raise


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


def fetch_event_details(event_id: str) -> dict | None:
    load_environment()
    if event_id.startswith("f1-"):
        return JolpicaF1Provider().details(event_id)
    if event_id.startswith("f4-"):
        return F4CalendarProvider().details(event_id)
    if event_id.startswith("cs2-"):
        return PandaScoreCS2Provider().details(event_id, get_event(event_id))
    return None


def provider_is_configured(provider: EventProvider) -> bool:
    if isinstance(provider, FootballDataProvider):
        return bool(provider.token)
    if isinstance(provider, ApiFootballProvider):
        return bool(provider.token)
    if isinstance(provider, TheSportsDBFootballProvider):
        return bool(provider.team_ids)
    if isinstance(provider, PandaScoreCS2Provider):
        return bool(provider.token)
    return True


def provider_should_refresh(provider_name: str, cache_key: str) -> bool:
    fetch_state = provider_fetch_state(provider_name, cache_key)
    if fetch_state is None:
        return True
    if fetch_state.status != "ok":
        return True
    ttl = PROVIDER_REFRESH_TTL.get(provider_name, timedelta(hours=1))
    return datetime.now(timezone.utc) - fetch_state.fetched_at >= ttl


def provider_matches_event(provider_name: str, event: Event) -> bool:
    if provider_name == "JolpicaF1Provider":
        return event.source == "jolpica"
    if provider_name == "F4CalendarProvider":
        return event.source == "f4-calendar"
    if provider_name in {"FootballDataProvider", "EspnFootballProvider", "ApiFootballProvider", "TheSportsDBFootballProvider"}:
        return event.sport == Sport.FOOTBALL
    if provider_name == "PandaScoreCS2Provider":
        return event.sport == Sport.CS2
    return False


def range_cache_key(start: datetime | None, end: datetime | None, preferences: UserPreferences | None = None) -> str:
    start_key = start.date().isoformat() if start else "default"
    end_key = end.date().isoformat() if end else "default"
    follow_key = "default"
    if preferences:
        followed = sorted(
            f"{entity_id}:{follow_level_value(follow.level)}"
            for entity_id, follow in preferences.follows.items()
            if is_active_follow_level(follow.level)
        )
        follow_key = "|".join(followed) or "none"
    return f"{start_key}:{end_key}:{follow_key}"


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
