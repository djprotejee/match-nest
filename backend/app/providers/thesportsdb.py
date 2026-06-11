from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .base import EventProvider
from .football_data import football_entity_ids, football_importance
from ..models import Event, EventStatus, Sport


THESPORTSDB_DEFAULT_KEY = "3"
THESPORTSDB_TEAM_IDS = {
    # TheSportsDB team id for the senior Ukraine men's national football team.
    # This keeps Ukraine NT fixtures available when football-data blocks the
    # team matches endpoint on the current subscription tier.
    "ukraine_nt": 133915,
}


class TheSportsDBFootballProvider(EventProvider):
    def __init__(
        self,
        team_ids: dict[str, int] | None = None,
        api_key: str | None = None,
        base_url: str = "https://www.thesportsdb.com/api/v1/json",
    ) -> None:
        self.team_ids = team_ids if team_ids is not None else THESPORTSDB_TEAM_IDS
        self.api_key = api_key or os.getenv("THESPORTSDB_API_KEY", THESPORTSDB_DEFAULT_KEY)
        self.base_url = base_url.rstrip("/")

    def fetch(self, start: datetime | None = None, end: datetime | None = None) -> list[Event]:
        events: list[Event] = []
        for entity_id, team_id in self.team_ids.items():
            events.extend(self._fetch_team_events(entity_id, team_id, start, end))
        return dedupe_events(events)

    def _fetch_team_events(
        self,
        entity_id: str,
        team_id: int,
        start: datetime | None,
        end: datetime | None,
    ) -> list[Event]:
        payloads: list[dict] = []
        for endpoint in ("eventsnext.php", "eventslast.php"):
            payloads.extend(self._request(endpoint, {"id": str(team_id)}).get("events") or [])

        events: list[Event] = []
        for item in payloads:
            event = self._event_from_payload(item, entity_id)
            if event is None:
                continue
            if start and event.starts_at and event.starts_at < start.astimezone(timezone.utc):
                continue
            if end and event.starts_at and event.starts_at > end.astimezone(timezone.utc):
                continue
            events.append(event)
        return events

    def _request(self, endpoint: str, params: dict[str, str]) -> dict:
        query = urlencode(params)
        url = f"{self.base_url}/{self.api_key}/{endpoint}?{query}"
        request = Request(url, headers={"User-Agent": "MatchNest personal schedule app"})
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))

    def _event_from_payload(self, item: dict, entity_id: str) -> Event | None:
        event_id = item.get("idEvent")
        starts_at = parse_thesportsdb_datetime(item)
        if not event_id or starts_at is None:
            return None

        home = item.get("strHomeTeam") or "Home"
        away = item.get("strAwayTeam") or "Away"
        competition = item.get("strLeague")
        entity_ids = football_entity_ids(home, away, competition)
        if entity_id not in entity_ids:
            entity_ids.append(entity_id)
        entity_ids = list(dict.fromkeys(entity_ids))

        return Event(
            id=f"football-tsdb-{event_id}",
            title=f"{home} vs {away}",
            sport=Sport.FOOTBALL,
            starts_at=starts_at,
            status=thesportsdb_status(item, starts_at),
            entity_ids=entity_ids,
            source="thesportsdb",
            competition=competition,
            result_summary=thesportsdb_score_summary(item, home, away),
            importance=football_importance(entity_ids),
        )


def parse_thesportsdb_datetime(item: dict) -> datetime | None:
    timestamp = item.get("strTimestamp")
    if timestamp:
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass

    date_value = item.get("dateEvent")
    if not date_value:
        return None
    time_value = (item.get("strTime") or "00:00:00").split("+")[0]
    try:
        return datetime.fromisoformat(f"{date_value}T{time_value}").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def thesportsdb_status(item: dict, starts_at: datetime) -> EventStatus:
    status = (item.get("strStatus") or "").upper()
    if status in {"FT", "AET", "AP"} or has_scores(item):
        return EventStatus.PAST
    if status in {"LIVE", "HT"}:
        return EventStatus.LIVE
    if starts_at <= datetime.now(timezone.utc):
        return EventStatus.DELAYED if status in {"NS", "TBD", ""} else EventStatus.PAST
    return EventStatus.UPCOMING


def thesportsdb_score_summary(item: dict, home: str, away: str) -> str | None:
    if not has_scores(item):
        return None
    return f"{home} {item.get('intHomeScore')}-{item.get('intAwayScore')} {away}"


def has_scores(item: dict) -> bool:
    return item.get("intHomeScore") is not None and item.get("intAwayScore") is not None


def dedupe_events(events: list[Event]) -> list[Event]:
    seen: set[str] = set()
    unique: list[Event] = []
    for event in events:
        if event.id in seen:
            continue
        seen.add(event.id)
        unique.append(event)
    return unique
