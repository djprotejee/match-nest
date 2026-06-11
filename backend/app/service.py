from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Iterable

from .models import Event, EventStatus, F1Session, FollowLevel, KYIV_TZ, Sport, UserPreferences


def visible_follow_level(event: Event, preferences: UserPreferences) -> FollowLevel:
    # One event can match several entities. Main should always win over starred
    # so a Ukraine match in UEFA Euro still lands in the primary feed.
    levels: list[FollowLevel] = []
    for entity_id in event.entity_ids:
        follow = preferences.follow_for(entity_id)
        if follow:
            levels.append(follow.level)

    if FollowLevel.HIDDEN in levels:
        return FollowLevel.HIDDEN
    if FollowLevel.MAIN in levels:
        return FollowLevel.MAIN
    if FollowLevel.STARRED in levels:
        return FollowLevel.STARRED
    if FollowLevel.MUTED in levels:
        return FollowLevel.MUTED
    return FollowLevel.EXPLORE


def should_show_event(
    event: Event,
    preferences: UserPreferences,
    levels: set[FollowLevel] | None = None,
    sports: set[Sport] | None = None,
    statuses: set[EventStatus] | None = None,
    apply_f1_session_filter: bool = True,
) -> bool:
    # Visibility is resolved before date filtering because hidden entities should
    # never leak into timeline, calendar, widget, or notification payloads.
    level = visible_follow_level(event, preferences)
    if level == FollowLevel.HIDDEN:
        return False
    if levels and level not in levels:
        return False
    if sports and event.sport not in sports:
        return False
    if statuses and event.status not in statuses:
        return False
    if apply_f1_session_filter and event.sport == Sport.FORMULA and event.session_type not in preferences.f1_sessions:
        return False
    return True


def filter_events(
    events: Iterable[Event],
    preferences: UserPreferences,
    start: datetime | None = None,
    end: datetime | None = None,
    levels: set[FollowLevel] | None = None,
    sports: set[Sport] | None = None,
    statuses: set[EventStatus] | None = None,
    apply_f1_session_filter: bool = True,
) -> list[Event]:
    result = []
    for event in events:
        if not should_show_event(event, preferences, levels, sports, statuses, apply_f1_session_filter):
            continue
        if event.starts_at is not None:
            event_start = event.starts_at.astimezone(KYIV_TZ)
            if start and event_start < start.astimezone(KYIV_TZ):
                continue
            if end and event_start >= end.astimezone(KYIV_TZ):
                continue
        result.append(event)
    return sorted(result, key=lambda item: (item.starts_at is None, item.starts_at or datetime.max.replace(tzinfo=KYIV_TZ), -item.importance))


def range_for_preset(preset: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    local_now = (now or datetime.now(KYIV_TZ)).astimezone(KYIV_TZ)
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    if preset == "today":
        return day_start, day_start + timedelta(days=1)
    if preset == "week":
        return day_start, day_start + timedelta(days=7)
    if preset == "month":
        return day_start.replace(day=1), _next_month(day_start.replace(day=1))
    raise ValueError(f"Unsupported range preset: {preset}")


def month_range(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1, tzinfo=KYIV_TZ)
    return start, _next_month(start)


def _next_month(start: datetime) -> datetime:
    if start.month == 12:
        return start.replace(year=start.year + 1, month=1)
    return start.replace(month=start.month + 1)


def serialize_event(event: Event, preferences: UserPreferences, reveal_spoilers: bool = False) -> dict:
    level = visible_follow_level(event, preferences)
    # Spoiler mode removes the result from API responses instead of only hiding
    # it in the UI. That keeps widgets and notifications spoiler-safe too.
    hide_result = (
        preferences.default_hide_spoilers
        and event.status == EventStatus.PAST
        and event.result_summary is not None
        and not reveal_spoilers
    )
    payload = asdict(event)
    payload["sport"] = event.sport.value
    payload["status"] = event.status.value
    payload["session_type"] = event.session_type.value if event.session_type else None
    payload["starts_at"] = event.starts_at.isoformat() if event.starts_at else None
    payload["follow_level"] = level.value
    payload["result_hidden"] = hide_result
    payload["result_summary"] = event.result_summary
    return payload


def group_by_day(events: Iterable[Event], preferences: UserPreferences, reveal_spoilers: bool = False) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        key = "tbd"
        if event.starts_at:
            key = event.starts_at.astimezone(KYIV_TZ).date().isoformat()
        grouped[key].append(serialize_event(event, preferences, reveal_spoilers))
    return [{"date": key, "events": value} for key, value in sorted(grouped.items())]


def parse_enum_set(values: str | None, enum_type):
    if not values:
        return None
    return {enum_type(value.strip()) for value in values.split(",") if value.strip()}


def update_f1_sessions(preferences: UserPreferences, values: Iterable[str]) -> UserPreferences:
    preferences.f1_sessions = {F1Session(value) for value in values}
    return preferences
