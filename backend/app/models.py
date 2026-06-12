from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo


try:
    KYIV_TZ = ZoneInfo("Europe/Kyiv")
except Exception:
    KYIV_TZ = timezone(timedelta(hours=3), name="Europe/Kyiv")


class Sport(str, Enum):
    FORMULA = "formula"
    CS2 = "cs2"
    FOOTBALL = "football"


class EntityKind(str, Enum):
    TEAM = "team"
    COMPETITION = "competition"
    PLAYER = "player"
    SESSION_TYPE = "session_type"


class FollowLevel(str, Enum):
    MAIN = "main"
    STARRED = "starred"
    MUTED = "muted"
    HIDDEN = "hidden"
    EXPLORE = "explore"


class EventStatus(str, Enum):
    PAST = "past"
    LIVE = "live"
    DELAYED = "delayed"
    UPCOMING = "upcoming"
    TBD = "tbd"


class F1Session(str, Enum):
    RACE = "race"
    QUALIFYING = "qualifying"
    SPRINT = "sprint"
    PRACTICE = "practice"


@dataclass(frozen=True)
class Entity:
    id: str
    name: str
    sport: Sport
    kind: EntityKind
    color: str


@dataclass
class Follow:
    entity_id: str
    level: str
    notifications_enabled: bool = True
    hide_spoilers: bool = True


@dataclass
class Event:
    id: str
    title: str
    sport: Sport
    starts_at: datetime | None
    status: EventStatus
    entity_ids: list[str]
    source: str
    competition: str | None = None
    session_type: F1Session | None = None
    result_summary: str | None = None
    importance: int = 50

    def is_on_day(self, day: datetime) -> bool:
        if self.starts_at is None:
            return False
        local_start = self.starts_at.astimezone(KYIV_TZ)
        local_day = day.astimezone(KYIV_TZ)
        return local_start.date() == local_day.date()


@dataclass
class UserPreferences:
    follows: dict[str, Follow] = field(default_factory=dict)
    f1_sessions: set[F1Session] = field(
        default_factory=lambda: {F1Session.RACE, F1Session.QUALIFYING, F1Session.SPRINT}
    )
    default_hide_spoilers: bool = True
    timezone: str = "Europe/Kyiv"
    ui_state: dict = field(default_factory=dict)

    def follow_for(self, entity_id: str) -> Follow | None:
        return self.follows.get(entity_id)


@dataclass(frozen=True)
class UserAccount:
    id: int
    email: str
    email_verified_at: datetime | None
    created_at: datetime

    @property
    def is_email_verified(self) -> bool:
        return self.email_verified_at is not None


@dataclass(frozen=True)
class EntityBinding:
    provider: str
    binding_type: str
    value: str
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class EntityRecord:
    entity: Entity
    aliases: list[str] = field(default_factory=list)
    bindings: list[EntityBinding] = field(default_factory=list)
    is_seed: bool = False
    created_by_user_id: int | None = None
