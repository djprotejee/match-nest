from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Iterable

from .models import Event, EventStatus, F1Session, FollowLevel, KYIV_TZ, Sport, UserPreferences


def follow_level_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def visible_follow_level(event: Event, preferences: UserPreferences) -> str:
    # One event can match several entities. Main should always win over starred
    # so a Ukraine match in UEFA Euro still lands in the primary feed.
    levels: list[str] = []
    for entity_id in event.entity_ids:
        follow = preferences.follow_for(entity_id)
        if follow:
            levels.append(follow_level_value(follow.level))

    if FollowLevel.HIDDEN.value in levels:
        return FollowLevel.HIDDEN.value
    if FollowLevel.MAIN.value in levels:
        return FollowLevel.MAIN.value
    if FollowLevel.STARRED.value in levels:
        return FollowLevel.STARRED.value
    custom_levels = [level for level in levels if level not in {FollowLevel.MUTED.value, FollowLevel.EXPLORE.value}]
    if custom_levels:
        return custom_levels[0]
    if FollowLevel.MUTED.value in levels:
        return FollowLevel.MUTED.value
    return FollowLevel.EXPLORE.value


def should_show_event(
    event: Event,
    preferences: UserPreferences,
    levels: set[str] | None = None,
    sports: set[Sport] | None = None,
    statuses: set[EventStatus] | None = None,
    apply_f1_session_filter: bool = True,
) -> bool:
    event.status = effective_event_status(event)
    # Visibility is resolved before date filtering because hidden entities should
    # never leak into timeline, calendar, widget, or notification payloads.
    level = visible_follow_level(event, preferences)
    if level == FollowLevel.HIDDEN.value:
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
    levels: set[str] | None = None,
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
    event.status = effective_event_status(event)
    level = visible_follow_level(event, preferences)
    # Spoiler mode removes the result from API responses instead of only hiding
    # it in the UI. That keeps widgets and notifications spoiler-safe too.
    hide_result = (
        preferences.default_hide_spoilers
        and event.status in {EventStatus.PAST, EventStatus.LIVE, EventStatus.DELAYED}
        and event.result_summary is not None
        and not reveal_spoilers
    )
    payload = asdict(event)
    payload["sport"] = event.sport.value
    payload["status"] = event.status.value
    payload["session_type"] = event.session_type.value if event.session_type else None
    payload["starts_at"] = event.starts_at.isoformat() if event.starts_at else None
    payload["follow_level"] = level
    payload["result_hidden"] = hide_result
    payload["result_summary"] = None if hide_result else event.result_summary
    return payload


def group_by_day(events: Iterable[Event], preferences: UserPreferences, reveal_spoilers: bool = False) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        key = "tbd"
        if event.starts_at:
            key = event.starts_at.astimezone(KYIV_TZ).date().isoformat()
        grouped[key].append(serialize_event(event, preferences, reveal_spoilers))
    return [{"date": key, "events": value} for key, value in sorted(grouped.items())]


def effective_event_status(event: Event, now: datetime | None = None) -> EventStatus:
    if event.starts_at is None:
        return event.status
    current = (now or datetime.now(KYIV_TZ)).astimezone(KYIV_TZ)
    starts_at = event.starts_at.astimezone(KYIV_TZ)
    if event.status == EventStatus.UPCOMING and starts_at <= current:
        return EventStatus.DELAYED
    if event.status == EventStatus.LIVE and current - starts_at > timedelta(hours=8):
        return EventStatus.PAST
    if event.status == EventStatus.DELAYED and current - starts_at > timedelta(days=2):
        return EventStatus.PAST
    return event.status


def parse_enum_set(values: str | None, enum_type):
    if not values:
        return None
    return {enum_type(value.strip()) for value in values.split(",") if value.strip()}


def parse_level_set(values: str | None) -> set[str] | None:
    if not values:
        return None
    return {value.strip() for value in values.split(",") if value.strip()}


def update_f1_sessions(preferences: UserPreferences, values: Iterable[str]) -> UserPreferences:
    preferences.f1_sessions = {F1Session(value) for value in values}
    return preferences
