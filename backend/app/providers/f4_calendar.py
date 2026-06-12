from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
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
        events = [self._event_for_weekend(weekend) for weekend in ITALIAN_F4_2026_WEEKENDS]
        if start:
            events = [event for event in events if event.starts_at and event.starts_at >= start.astimezone(timezone.utc)]
        if end:
            events = [event for event in events if event.starts_at and event.starts_at < end.astimezone(timezone.utc)]
        return events

    def _event_for_weekend(self, weekend: F4Weekend) -> Event:
        starts_at = datetime.combine(datetime.fromisoformat(weekend.start_date).date(), time(hour=12), ROME_TZ)
        return Event(
            id=f"f4-italian-2026-{weekend.slug}",
            title=f"Italian F4 - {weekend.name}",
            sport=Sport.FORMULA,
            starts_at=starts_at.astimezone(timezone.utc),
            status=status_from_start(starts_at),
            entity_ids=["bondarev", "italian_f4"],
            source=self.source,
            competition="Italian F4 Championship",
            result_summary=f"{weekend.circuit}, {weekend.start_date} - {weekend.end_date}",
            importance=72,
        )


def status_from_start(starts_at: datetime) -> EventStatus:
    return EventStatus.PAST if starts_at.astimezone(timezone.utc) < datetime.now(timezone.utc) else EventStatus.UPCOMING


ITALIAN_F4_2026_WEEKENDS = [
    F4Weekend("misano-1", "Misano", "Misano World Circuit", "2026-05-09", "2026-05-10"),
    F4Weekend("vallelunga", "Vallelunga", "Vallelunga Circuit", "2026-05-22", "2026-05-24"),
    F4Weekend("monza", "Monza", "Monza Circuit", "2026-06-19", "2026-06-21"),
    F4Weekend("mugello-1", "Mugello", "Mugello Circuit", "2026-07-24", "2026-07-26"),
    F4Weekend("imola", "Imola", "Imola Circuit", "2026-09-04", "2026-09-06"),
    F4Weekend("misano-2", "Misano", "Misano World Circuit", "2026-09-18", "2026-09-20"),
    F4Weekend("mugello-2", "Mugello", "Mugello Circuit", "2026-10-30", "2026-11-01"),
]
