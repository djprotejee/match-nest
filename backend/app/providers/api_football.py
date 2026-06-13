from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .base import EventProvider
from .football_data import (
    football_entity_ids,
    football_importance,
    normalize_team_name,
    parse_entity_id_map,
)
from ..models import Event, EventStatus, Sport
from ..storage import get_cached_provider_payload, provider_payload_state, upsert_provider_payload_cache

API_FOOTBALL_CACHE_TTL = timedelta(hours=12)
API_FOOTBALL_TEAM_CACHE_TTL = timedelta(days=30)


class ApiFootballProvider(EventProvider):
    def __init__(
        self,
        token: str | None = None,
        team_queries: dict[str, list[str]] | None = None,
        team_entities: dict[str, int] | None = None,
        base_url: str = "https://v3.football.api-sports.io",
    ) -> None:
        self.token = token or os.getenv("API_FOOTBALL_TOKEN")
        self.team_queries = team_queries or {}
        self.team_entities = team_entities or api_football_team_entities()
        self.base_url = base_url.rstrip("/")

    def fetch(self, start: datetime | None = None, end: datetime | None = None) -> list[Event]:
        if not self.token:
            return []

        start_date = (start.astimezone(timezone.utc) if start else datetime.now(timezone.utc)).date()
        end_date = (end.astimezone(timezone.utc) if end else datetime.now(timezone.utc)).date()
        events: list[Event] = []

        for entity_id, team_id in self.resolved_team_entities().items():
            for season in api_football_seasons(start_date, end_date):
                params = {
                    "team": str(team_id),
                    "season": str(season),
                    "from": start_date.isoformat(),
                    "to": end_date.isoformat(),
                }
                fixtures = self._cached_request("fixtures", params, API_FOOTBALL_CACHE_TTL).get("response", [])
                for item in fixtures:
                    event = self._fixture_to_event(item, entity_id)
                    if event is not None:
                        events.append(event)

        return dedupe_events(events)

    def resolved_team_entities(self) -> dict[str, int]:
        resolved = dict(self.team_entities)
        for entity_id, queries in self.team_queries.items():
            if entity_id in resolved:
                continue
            team_id = self._find_team_id(queries)
            if team_id is not None:
                resolved[entity_id] = team_id
        return resolved

    def _find_team_id(self, queries: list[str]) -> int | None:
        normalized_queries = {normalize_team_name(query) for query in queries}
        for query in queries:
            try:
                payload = self._cached_request("teams", {"search": query}, API_FOOTBALL_TEAM_CACHE_TTL)
            except HTTPError:
                continue
            candidates = payload.get("response", [])
            preferred = [item for item in candidates if team_matches_query(item, normalized_queries, national=True)]
            fallback = [item for item in candidates if team_matches_query(item, normalized_queries, national=False)]
            for item in preferred or fallback:
                team = item.get("team", {})
                return team.get("id")
        return None

    def _cached_request(self, endpoint: str, params: dict[str, str], max_age: timedelta) -> dict:
        path = api_football_cache_path(endpoint, params)
        cache_key = api_football_cache_key(endpoint, params)
        db_cached = read_fresh_api_football_payload(cache_key, max_age)
        if db_cached is not None:
            return db_cached
        cached = read_api_football_cache(path, max_age)
        if cached is not None:
            return cached
        try:
            payload = self._request(endpoint, params)
        except Exception:
            stale = read_api_football_cache(path, None)
            if stale is None:
                stale = read_api_football_payload(cache_key)
            if stale is not None:
                return stale
            raise
        write_api_football_cache(path, payload)
        upsert_provider_payload_cache("api-football", cache_key, payload)
        return payload

    def _request(self, endpoint: str, params: dict[str, str]) -> dict:
        url = f"{self.base_url}/{endpoint}?{urlencode(params)}"
        request = Request(url, headers={"x-apisports-key": self.token or ""})
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        errors = payload.get("errors")
        if errors:
            raise RuntimeError(f"API-Football {endpoint} error: {errors}")
        return payload

    def _fixture_to_event(self, item: dict, followed_entity_id: str) -> Event | None:
        fixture = item.get("fixture", {})
        fixture_id = fixture.get("id")
        date_value = fixture.get("date")
        if fixture_id is None or not date_value:
            return None

        starts_at = datetime.fromisoformat(date_value.replace("Z", "+00:00")).astimezone(timezone.utc)
        teams = item.get("teams", {})
        home = teams.get("home", {}).get("name") or "Home"
        away = teams.get("away", {}).get("name") or "Away"
        league = item.get("league", {})
        competition = league.get("name")
        entity_ids = football_entity_ids(home, away, competition)
        if followed_entity_id not in entity_ids:
            entity_ids.append(followed_entity_id)
        entity_ids = list(dict.fromkeys(entity_ids))

        return Event(
            id=f"football-apifootball-{fixture_id}",
            title=f"{home} vs {away}",
            sport=Sport.FOOTBALL,
            starts_at=starts_at,
            status=api_football_status(fixture.get("status", {}), starts_at),
            entity_ids=entity_ids,
            source="api-football",
            competition=competition,
            result_summary=api_football_score_summary(item, home, away),
            importance=football_importance(entity_ids),
        )


def api_football_team_entities() -> dict[str, int]:
    return parse_entity_id_map(os.getenv("API_FOOTBALL_TEAM_IDS", ""))


def api_football_cache_path(endpoint: str, params: dict[str, str]) -> Path:
    safe = "_".join(f"{key}-{value}" for key, value in sorted(params.items()))
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in safe)
    return Path(__file__).resolve().parents[2] / ".cache" / f"api-football-{endpoint}-{safe}.json"


def api_football_cache_key(endpoint: str, params: dict[str, str]) -> str:
    safe = urlencode(sorted(params.items()))
    return f"{endpoint}:{safe}"


def read_fresh_api_football_payload(cache_key: str, max_age: timedelta) -> dict | None:
    state = provider_payload_state("api-football", cache_key)
    if state is None or datetime.now(timezone.utc) - state.fetched_at > max_age:
        return None
    return state.payload if isinstance(state.payload, dict) else None


def read_api_football_payload(cache_key: str) -> dict | None:
    payload = get_cached_provider_payload("api-football", cache_key)
    return payload if isinstance(payload, dict) else None


def read_api_football_cache(path: Path, max_age: timedelta | None) -> dict | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if max_age is not None:
        cached_at = payload.get("cached_at")
        if not cached_at:
            return None
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(cached_at).astimezone(timezone.utc)
        except ValueError:
            return None
        if age > max_age:
            return None
    return payload.get("payload")


def write_api_football_cache(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"cached_at": datetime.now(timezone.utc).isoformat(), "payload": payload}, ensure_ascii=False),
        encoding="utf-8",
    )


def api_football_seasons(start_date: date, end_date: date) -> list[int]:
    # API-Football fixtures require an explicit season. Calendar ranges can
    # cross a season boundary, so query the possible football seasons involved.
    seasons = {start_date.year, end_date.year}
    if start_date.month <= 6:
        seasons.add(start_date.year - 1)
    if end_date.month <= 6:
        seasons.add(end_date.year - 1)
    return sorted(seasons)


def team_matches_query(item: dict, normalized_queries: set[str], national: bool) -> bool:
    team = item.get("team", {})
    if bool(team.get("national")) != national:
        return False
    names = [
        team.get("name"),
        team.get("code"),
    ]
    normalized_names = {normalize_team_name(name) for name in names if name}
    return bool(normalized_queries & normalized_names)


def api_football_status(status: dict, starts_at: datetime) -> EventStatus:
    short = status.get("short")
    if short in {"1H", "HT", "2H", "ET", "BT", "P", "INT", "LIVE"}:
        return EventStatus.LIVE
    if short in {"FT", "AET", "PEN"}:
        return EventStatus.PAST
    if short in {"TBD"}:
        return EventStatus.TBD
    if short in {"PST", "CANC", "ABD", "AWD", "WO"}:
        return EventStatus.DELAYED
    if starts_at <= datetime.now(timezone.utc):
        return EventStatus.DELAYED if short in {"NS", None} else EventStatus.PAST
    return EventStatus.UPCOMING


def api_football_score_summary(item: dict, home: str, away: str) -> str | None:
    status = item.get("fixture", {}).get("status", {}).get("short")
    if status not in {"FT", "AET", "PEN"}:
        return None
    goals = item.get("goals", {})
    home_score = goals.get("home")
    away_score = goals.get("away")
    if home_score is None or away_score is None:
        return None
    return f"{home} {home_score}-{away_score} {away}"


def dedupe_events(events: list[Event]) -> list[Event]:
    unique: dict[str, Event] = {}
    for event in events:
        unique[event.id] = event
    return list(unique.values())
