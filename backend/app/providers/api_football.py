from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
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
                fixtures = self._request(
                    "fixtures",
                    {
                        "team": str(team_id),
                        "season": str(season),
                        "from": start_date.isoformat(),
                        "to": end_date.isoformat(),
                    },
                ).get("response", [])
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
                payload = self._request("teams", {"search": query})
            except HTTPError:
                continue
            candidates = payload.get("response", [])
            preferred = [item for item in candidates if team_matches_query(item, normalized_queries, national=True)]
            fallback = [item for item in candidates if team_matches_query(item, normalized_queries, national=False)]
            for item in preferred or fallback:
                team = item.get("team", {})
                return team.get("id")
        return None

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
