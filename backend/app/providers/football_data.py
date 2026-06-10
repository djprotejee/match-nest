from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from .base import EventProvider
from ..models import Event, EventStatus, Sport


class FootballDataProvider(EventProvider):
    def __init__(
        self,
        token: str | None = None,
        competitions: str = "PD,CL,WC,EC",
        url: str = "https://api.football-data.org/v4/matches",
    ) -> None:
        self.token = token or os.getenv("FOOTBALL_DATA_TOKEN")
        self.competitions = competitions
        self.url = url

    def fetch(self) -> list[Event]:
        # The free plan is enough for fixtures and delayed schedules. The token
        # stays backend-only and is never sent to the iOS client.
        if not self.token:
            return []
        request = Request(
            f"{self.url}?competitions={self.competitions}",
            headers={"X-Auth-Token": self.token},
        )
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))

        return [self._match_to_event(item) for item in payload.get("matches", [])]

    def _match_to_event(self, item: dict) -> Event:
        home = item.get("homeTeam", {}).get("name", "Home")
        away = item.get("awayTeam", {}).get("name", "Away")
        competition = item.get("competition", {}).get("name")
        entity_ids = football_entity_ids(home, away, competition)
        starts_at = datetime.fromisoformat(item["utcDate"].replace("Z", "+00:00")).astimezone(timezone.utc)

        return Event(
            id=f"football-{item.get('id')}",
            title=f"{home} vs {away}",
            sport=Sport.FOOTBALL,
            starts_at=starts_at,
            status=football_status(item.get("status"), starts_at),
            entity_ids=entity_ids,
            source="football-data",
            competition=competition,
            result_summary=score_summary(item, home, away),
            importance=football_importance(entity_ids),
        )


def football_entity_ids(home: str, away: str, competition: str | None) -> list[str]:
    # Entity mapping intentionally uses names instead of provider-specific IDs at
    # this stage. It keeps the first version resilient across competition feeds.
    names = f"{home} {away}".lower()
    entities: list[str] = []
    if "barcelona" in names:
        entities.append("barcelona")
    if "ukraine" in names:
        entities.append("ukraine_nt")
    competition_name = (competition or "").lower()
    if "champions league" in competition_name:
        entities.append("ucl")
    if "world cup" in competition_name:
        entities.append("world_cup")
    if "euro" in competition_name:
        entities.append("euro")
    return entities or ["football_explore"]


def football_status(status: str | None, starts_at: datetime) -> EventStatus:
    if status in {"IN_PLAY", "PAUSED"}:
        return EventStatus.LIVE
    if status == "FINISHED":
        return EventStatus.PAST
    if starts_at <= datetime.now(timezone.utc):
        return EventStatus.PAST
    return EventStatus.UPCOMING


def score_summary(item: dict, home: str, away: str) -> str | None:
    # Past scores are generated here, then stripped by serialize_event when
    # spoiler mode is active.
    if item.get("status") != "FINISHED":
        return None
    full_time = item.get("score", {}).get("fullTime", {})
    home_score = full_time.get("home")
    away_score = full_time.get("away")
    if home_score is None or away_score is None:
        return None
    return f"{home} {home_score}-{away_score} {away}"


def football_importance(entity_ids: list[str]) -> int:
    if "barcelona" in entity_ids or "ukraine_nt" in entity_ids:
        return 90
    if any(item in entity_ids for item in ["ucl", "world_cup", "euro"]):
        return 75
    return 45
