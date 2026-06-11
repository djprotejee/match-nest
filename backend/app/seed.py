from __future__ import annotations

from datetime import datetime, timedelta

from .models import (
    Entity,
    EntityKind,
    Event,
    EventStatus,
    F1Session,
    Follow,
    FollowLevel,
    KYIV_TZ,
    Sport,
    UserPreferences,
)


ENTITIES: dict[str, Entity] = {
    "f1": Entity("f1", "Formula 1", Sport.FORMULA, EntityKind.COMPETITION, "#F04438"),
    "ferrari": Entity("ferrari", "Ferrari", Sport.FORMULA, EntityKind.TEAM, "#DC2626"),
    "bondarev": Entity("bondarev", "Oleksandr Bondarev", Sport.FORMULA, EntityKind.PLAYER, "#F04438"),
    "navi_cs2": Entity("navi_cs2", "NAVI CS2", Sport.CS2, EntityKind.TEAM, "#F4B740"),
    "cs2_majors": Entity("cs2_majors", "CS2 Majors", Sport.CS2, EntityKind.COMPETITION, "#F59E0B"),
    "iem": Entity("iem", "IEM", Sport.CS2, EntityKind.COMPETITION, "#F59E0B"),
    "blast": Entity("blast", "BLAST", Sport.CS2, EntityKind.COMPETITION, "#F59E0B"),
    "ukraine_nt": Entity("ukraine_nt", "Ukraine NT", Sport.FOOTBALL, EntityKind.TEAM, "#2ECC71"),
    "barcelona": Entity("barcelona", "FC Barcelona", Sport.FOOTBALL, EntityKind.TEAM, "#2ECC71"),
    "ucl": Entity("ucl", "UEFA Champions League", Sport.FOOTBALL, EntityKind.COMPETITION, "#8B5CF6"),
    "world_cup": Entity("world_cup", "FIFA World Cup", Sport.FOOTBALL, EntityKind.COMPETITION, "#8B5CF6"),
    "euro": Entity("euro", "UEFA Euro", Sport.FOOTBALL, EntityKind.COMPETITION, "#8B5CF6"),
    "football_explore": Entity("football_explore", "Football Explore", Sport.FOOTBALL, EntityKind.COMPETITION, "#2ECC71"),
    "cs2_explore": Entity("cs2_explore", "CS2 Explore", Sport.CS2, EntityKind.COMPETITION, "#F4B740"),
}


DEFAULT_PREFERENCES = UserPreferences(
    follows={
        "f1": Follow("f1", FollowLevel.MAIN),
        "navi_cs2": Follow("navi_cs2", FollowLevel.MAIN),
        "ukraine_nt": Follow("ukraine_nt", FollowLevel.MAIN),
        "barcelona": Follow("barcelona", FollowLevel.MAIN),
        "ferrari": Follow("ferrari", FollowLevel.STARRED),
        "cs2_majors": Follow("cs2_majors", FollowLevel.STARRED),
        "iem": Follow("iem", FollowLevel.STARRED),
        "blast": Follow("blast", FollowLevel.STARRED),
        "ucl": Follow("ucl", FollowLevel.STARRED),
        "world_cup": Follow("world_cup", FollowLevel.STARRED),
        "euro": Follow("euro", FollowLevel.STARRED),
    }
)


def demo_events(now: datetime | None = None) -> list[Event]:
    base = (now or datetime.now(KYIV_TZ)).astimezone(KYIV_TZ)
    yesterday = base - timedelta(days=1)
    tomorrow = base + timedelta(days=1)
    next_week = base + timedelta(days=6)
    next_month = base + timedelta(days=31)
    prev_month = base - timedelta(days=20)

    return [
        Event(
            id="f1-race-demo",
            title="Formula 1 Grand Prix - Race",
            sport=Sport.FORMULA,
            starts_at=tomorrow.replace(hour=16, minute=0, second=0, microsecond=0),
            status=EventStatus.UPCOMING,
            entity_ids=["f1", "ferrari"],
            source="demo",
            competition="Formula 1",
            session_type=F1Session.RACE,
            importance=95,
        ),
        Event(
            id="f1-practice-demo",
            title="Formula 1 Grand Prix - Practice 1",
            sport=Sport.FORMULA,
            starts_at=tomorrow.replace(hour=12, minute=30, second=0, microsecond=0),
            status=EventStatus.UPCOMING,
            entity_ids=["f1"],
            source="demo",
            competition="Formula 1",
            session_type=F1Session.PRACTICE,
            importance=35,
        ),
        Event(
            id="navi-live-demo",
            title="NAVI vs Vitality",
            sport=Sport.CS2,
            starts_at=base.replace(minute=0, second=0, microsecond=0),
            status=EventStatus.LIVE,
            entity_ids=["navi_cs2", "blast"],
            source="demo",
            competition="BLAST",
            importance=90,
        ),
        Event(
            id="barca-past-demo",
            title="Barcelona vs Real Madrid",
            sport=Sport.FOOTBALL,
            starts_at=yesterday.replace(hour=22, minute=0, second=0, microsecond=0),
            status=EventStatus.PAST,
            entity_ids=["barcelona"],
            source="demo",
            competition="La Liga",
            result_summary="Barcelona 2-1 Real Madrid",
            importance=88,
        ),
        Event(
            id="ukraine-next-demo",
            title="Ukraine vs Italy",
            sport=Sport.FOOTBALL,
            starts_at=next_week.replace(hour=21, minute=45, second=0, microsecond=0),
            status=EventStatus.UPCOMING,
            entity_ids=["ukraine_nt", "euro"],
            source="demo",
            competition="UEFA Euro",
            importance=92,
        ),
        Event(
            id="ucl-prev-month-demo",
            title="Champions League Final",
            sport=Sport.FOOTBALL,
            starts_at=prev_month.replace(hour=22, minute=0, second=0, microsecond=0),
            status=EventStatus.PAST,
            entity_ids=["ucl"],
            source="demo",
            competition="UEFA Champions League",
            result_summary="Result hidden in spoiler mode",
            importance=80,
        ),
        Event(
            id="major-next-month-demo",
            title="CS2 Major - Playoffs",
            sport=Sport.CS2,
            starts_at=next_month.replace(hour=18, minute=0, second=0, microsecond=0),
            status=EventStatus.UPCOMING,
            entity_ids=["cs2_majors"],
            source="demo",
            competition="CS2 Major",
            importance=82,
        ),
    ]
