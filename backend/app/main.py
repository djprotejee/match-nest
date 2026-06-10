from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from .models import EventStatus, F1Session, Follow, FollowLevel, KYIV_TZ, Sport
from .providers.registry import fetch_events
from .seed import DEFAULT_PREFERENCES, ENTITIES
from .service import (
    filter_events,
    group_by_day,
    month_range,
    parse_enum_set,
    range_for_preset,
    serialize_event,
    update_f1_sessions,
)

app = FastAPI(title="MatchNest API", version="0.1.0")

preferences = DEFAULT_PREFERENCES


class FollowUpdate(BaseModel):
    level: FollowLevel
    notifications_enabled: bool = True
    hide_spoilers: bool = True


class F1SessionsUpdate(BaseModel):
    sessions: list[F1Session]


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "timezone": "Europe/Kyiv"}


@app.get("/entities")
def entities() -> list[dict]:
    return [
        {
            "id": entity.id,
            "name": entity.name,
            "sport": entity.sport.value,
            "kind": entity.kind.value,
            "color": entity.color,
            "follow": preferences.follows.get(entity.id).level.value if entity.id in preferences.follows else "explore",
        }
        for entity in ENTITIES.values()
    ]


@app.get("/events")
def events(
    range: str = Query(default="week", pattern="^(today|week|month)$"),
    level: str | None = None,
    sport: str | None = None,
    status: str | None = None,
    reveal_spoilers: bool = False,
) -> list[dict]:
    try:
        start, end = range_for_preset(range)
        filtered = filter_events(
            fetch_events(),
            preferences,
            start=start,
            end=end,
            levels=parse_enum_set(level, FollowLevel),
            sports=parse_enum_set(sport, Sport),
            statuses=parse_enum_set(status, EventStatus),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [serialize_event(event, preferences, reveal_spoilers) for event in filtered]


@app.get("/timeline")
def timeline(
    range: str = Query(default="week", pattern="^(today|week|month)$"),
    level: str | None = "main,starred",
    reveal_spoilers: bool = False,
) -> list[dict]:
    start, end = range_for_preset(range)
    filtered = filter_events(
        fetch_events(),
        preferences,
        start=start,
        end=end,
        levels=parse_enum_set(level, FollowLevel),
    )
    return group_by_day(filtered, preferences, reveal_spoilers)


@app.get("/calendar/{year}/{month}")
def calendar_month(year: int, month: int, reveal_spoilers: bool = False) -> list[dict]:
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="Month must be between 1 and 12.")
    start, end = month_range(year, month)
    filtered = filter_events(
        fetch_events(),
        preferences,
        start=start,
        end=end,
        levels={FollowLevel.MAIN, FollowLevel.STARRED, FollowLevel.MUTED, FollowLevel.EXPLORE},
    )
    return group_by_day(filtered, preferences, reveal_spoilers)


@app.get("/widget/next")
def widget_next() -> dict:
    now = datetime.now(KYIV_TZ)
    filtered = filter_events(
        fetch_events(),
        preferences,
        start=now,
        levels={FollowLevel.MAIN, FollowLevel.STARRED},
        statuses={EventStatus.LIVE, EventStatus.UPCOMING},
    )
    if not filtered:
        return {"event": None}
    return {"event": serialize_event(filtered[0], preferences)}


@app.put("/follows/{entity_id}")
def set_follow(entity_id: str, update: FollowUpdate) -> dict:
    if entity_id not in ENTITIES:
        raise HTTPException(status_code=404, detail="Entity not found.")
    preferences.follows[entity_id] = Follow(
        entity_id=entity_id,
        level=update.level,
        notifications_enabled=update.notifications_enabled,
        hide_spoilers=update.hide_spoilers,
    )
    return {"ok": True, "entity_id": entity_id, "level": update.level.value}


@app.put("/settings/f1-sessions")
def set_f1_sessions(update: F1SessionsUpdate) -> dict:
    update_f1_sessions(preferences, [session.value for session in update.sessions])
    return {"ok": True, "sessions": [session.value for session in preferences.f1_sessions]}
