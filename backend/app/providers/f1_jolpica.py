from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.request import urlopen

from .base import EventProvider
from ..models import Event, EventStatus, F1Session, Sport


class JolpicaF1Provider(EventProvider):
    def __init__(self, url: str = "https://api.jolpi.ca/ergast/f1/current.json") -> None:
        self.url = url

    def fetch(self) -> list[Event]:
        with urlopen(self.url, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))

        races = payload.get("MRData", {}).get("RaceTable", {}).get("Races", [])
        events: list[Event] = []
        for race in races:
            race_name = race.get("raceName", "Formula 1 Grand Prix")
            round_id = race.get("round", "0")
            events.extend(self._race_sessions(race, race_name, round_id))
        return events

    def _race_sessions(self, race: dict, race_name: str, round_id: str) -> list[Event]:
        # Jolpica exposes a race weekend as one race object with nested session
        # timestamps. MatchNest stores each session as a separate event so the
        # user can hide practice but keep qualifying, sprint, and race alerts.
        sessions: list[tuple[str, str, F1Session, int]] = [
            ("race", race_name, F1Session.RACE, 95),
            ("Qualifying", f"{race_name} - Qualifying", F1Session.QUALIFYING, 82),
            ("Sprint", f"{race_name} - Sprint", F1Session.SPRINT, 78),
            ("SprintQualifying", f"{race_name} - Sprint Qualifying", F1Session.QUALIFYING, 70),
            ("FirstPractice", f"{race_name} - Practice 1", F1Session.PRACTICE, 35),
            ("SecondPractice", f"{race_name} - Practice 2", F1Session.PRACTICE, 30),
            ("ThirdPractice", f"{race_name} - Practice 3", F1Session.PRACTICE, 30),
        ]

        output: list[Event] = []
        for key, title, session_type, importance in sessions:
            source = race if key == "race" else race.get(key)
            starts_at = parse_utc_datetime(source)
            if starts_at is None:
                continue
            output.append(
                Event(
                    id=f"f1-{race.get('season', 'current')}-{round_id}-{key.lower()}",
                    title=title,
                    sport=Sport.FORMULA,
                    starts_at=starts_at,
                    status=status_for(starts_at),
                    entity_ids=["f1", "ferrari"],
                    source="jolpica",
                    competition="Formula 1",
                    session_type=session_type,
                    importance=importance,
                )
            )
        return output


def parse_utc_datetime(item: dict | None) -> datetime | None:
    # Ergast-compatible payloads split date and time. Time is UTC when present.
    if not item or "date" not in item:
        return None
    time_value = item.get("time", "00:00:00Z")
    value = f"{item['date']}T{time_value}".replace("Z", "+00:00")
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def status_for(starts_at: datetime) -> EventStatus:
    now = datetime.now(timezone.utc)
    if starts_at <= now:
        return EventStatus.PAST
    return EventStatus.UPCOMING
