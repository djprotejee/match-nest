from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .base import EventProvider
from ..models import Event, EventStatus, Sport


ROME_TZ = ZoneInfo("Europe/Rome")


@dataclass(frozen=True)
class F4Weekend:
    slug: str
    name: str
    circuit: str
    start_date: str
    end_date: str


class F4CalendarProvider(EventProvider):
    """Small free calendar provider for driver-focused Formula 4 tracking.

    F4 calendars do not have a stable public equivalent of Jolpica/Ergast.
    This provider keeps race weekends visible for followed junior drivers while
    detailed session timing can be added later through provider bindings.
    """

    source = "f4-calendar"

    def fetch(self, start: datetime | None = None, end: datetime | None = None) -> list[Event]:
        events = [event for weekend in ITALIAN_F4_2026_WEEKENDS for event in self._events_for_weekend(weekend)]
        if start:
            events = [event for event in events if event.starts_at and event.starts_at >= start.astimezone(timezone.utc)]
        if end:
            events = [event for event in events if event.starts_at and event.starts_at < end.astimezone(timezone.utc)]
        return events

    def details(self, event_id: str) -> dict | None:
        weekend, day_number = self._match_event(event_id)
        if not weekend:
            return None
        day_label = f"Day {day_number}" if day_number else "Race weekend"
        return {
            "event_id": event_id,
            "sport": Sport.FORMULA.value,
            "source": self.source,
            "summary": f"{weekend.name} {day_label} for the Italian F4 Championship.",
            "facts": [
                {"label": "Series", "value": "Italian F4 Championship"},
                {"label": "Tracked driver", "value": "Oleksandr Bondarev"},
                {"label": "Circuit", "value": weekend.circuit},
                {"label": "Weekend", "value": f"{weekend.start_date} - {weekend.end_date}"},
                {
                    "label": "Schedule precision",
                    "value": "Day-level calendar. Exact session timetable is not available from this free provider yet.",
                },
            ],
            "sections": [],
        }

    def _events_for_weekend(self, weekend: F4Weekend) -> list[Event]:
        start_date = datetime.fromisoformat(weekend.start_date).date()
        end_date = datetime.fromisoformat(weekend.end_date).date()
        day_count = (end_date - start_date).days + 1
        events: list[Event] = []
        for offset in range(day_count):
            current_date = start_date + timedelta(days=offset)
            starts_at = datetime.combine(current_date, time(hour=12), ROME_TZ)
            day_number = offset + 1
            events.append(
                Event(
                    id=f"f4-italian-2026-{weekend.slug}-day-{day_number}",
                    title=f"Italian F4 - {weekend.name} Day {day_number}",
                    sport=Sport.FORMULA,
                    starts_at=starts_at.astimezone(timezone.utc),
                    status=status_from_date(current_date),
                    entity_ids=["bondarev", "italian_f4"],
                    source=self.source,
                    competition="Italian F4 Championship",
                    result_summary=f"{weekend.circuit}, {weekend.start_date} - {weekend.end_date}",
                    importance=72,
                )
            )
        return events

    def _match_event(self, event_id: str) -> tuple[F4Weekend | None, int | None]:
        for weekend in ITALIAN_F4_2026_WEEKENDS:
            prefix = f"f4-italian-2026-{weekend.slug}"
            if event_id == prefix:
                return weekend, None
            if event_id.startswith(f"{prefix}-day-"):
                try:
                    return weekend, int(event_id.removeprefix(f"{prefix}-day-"))
                except ValueError:
                    return weekend, None
        return None, None


def status_from_date(event_date) -> EventStatus:
    today = datetime.now(ROME_TZ).date()
    return EventStatus.PAST if event_date < today else EventStatus.UPCOMING


ITALIAN_F4_2026_WEEKENDS = [
    F4Weekend("misano-1", "Misano", "Misano World Circuit", "2026-05-09", "2026-05-10"),
    F4Weekend("vallelunga", "Vallelunga", "Vallelunga Circuit", "2026-05-22", "2026-05-24"),
    F4Weekend("monza", "Monza", "Monza Circuit", "2026-06-19", "2026-06-21"),
    F4Weekend("mugello-1", "Mugello", "Mugello Circuit", "2026-07-24", "2026-07-26"),
    F4Weekend("imola", "Imola", "Imola Circuit", "2026-09-04", "2026-09-06"),
    F4Weekend("misano-2", "Misano", "Misano World Circuit", "2026-09-18", "2026-09-20"),
    F4Weekend("mugello-2", "Mugello", "Mugello Circuit", "2026-10-30", "2026-11-01"),
]
