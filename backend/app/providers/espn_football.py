from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .base import EventProvider
from .football_data import football_entity_ids, football_importance
from ..models import Event, EventStatus, Sport
from ..storage import get_cached_provider_payload, upsert_provider_payload_cache


ESPN_FOOTBALL_TEAMS = {
    "barcelona": {
        "ids": {"83"},
        "leagues": ["esp.1", "uefa.champions"],
    },
    "ukraine_nt": {
        "ids": {"457"},
        "leagues": ["uefa.nations", "fifa.world", "uefa.euro"],
    },
}

ESPN_FOOTBALL_COMPETITIONS = {
    "ucl": "uefa.champions",
    "world_cup": "fifa.world",
    "euro": "uefa.euro",
}


class EspnFootballProvider(EventProvider):
    def __init__(
        self,
        team_ids: dict[str, set[str]] | None = None,
        competition_entities: dict[str, str] | None = None,
        base_url: str = "https://site.api.espn.com/apis/site/v2/sports/soccer",
    ) -> None:
        self.team_ids = team_ids or {}
        self.competition_entities = competition_entities or {}
        self.base_url = base_url.rstrip("/")

    def fetch(self, start: datetime | None = None, end: datetime | None = None) -> list[Event]:
        start_utc = start.astimezone(timezone.utc) if start else datetime.now(timezone.utc)
        end_utc = end.astimezone(timezone.utc) if end else start_utc
        events: list[Event] = []

        for league_slug in self._league_slugs():
            for month_key in month_keys(start_utc, end_utc):
                for item in self._scoreboard(league_slug, month_key):
                    event = self._event_from_payload(item, league_slug)
                    if event is None or not event_in_range(event, start_utc, end_utc):
                        continue
                    if self._should_keep_event(item, event):
                        events.append(event)

        return dedupe_events(events)

    def _league_slugs(self) -> list[str]:
        slugs: set[str] = set(self.competition_entities.values())
        for entity_id in self.team_ids:
            slugs.update(ESPN_FOOTBALL_TEAMS.get(entity_id, {}).get("leagues", []))
        return sorted(slugs)

    def _scoreboard(self, league_slug: str, month_key: str) -> list[dict]:
        url = f"{self.base_url}/{league_slug}/scoreboard?{urlencode({'dates': month_key})}"
        cache_key = f"scoreboard:{league_slug}:{month_key}"
        request = Request(url, headers={"User-Agent": "MatchNest personal schedule app", "Accept": "application/json"})
        try:
            with urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            cached = get_cached_provider_payload("espn-football", cache_key)
            payload = cached if isinstance(cached, dict) else {}
        else:
            upsert_provider_payload_cache("espn-football", cache_key, payload)
        return payload.get("events") or []

    def _should_keep_event(self, item: dict, event: Event) -> bool:
        if any(entity_id in event.entity_ids for entity_id in self.competition_entities):
            return True
        event_team_ids = espn_event_team_ids(item)
        for entity_id, team_ids in self.team_ids.items():
            if event_team_ids & team_ids:
                return True
        return False

    def _event_from_payload(self, item: dict, league_slug: str) -> Event | None:
        event_id = item.get("id")
        date_value = item.get("date")
        if not event_id or not date_value:
            return None
        starts_at = datetime.fromisoformat(date_value.replace("Z", "+00:00")).astimezone(timezone.utc)
        competitors = espn_competitors(item)
        if len(competitors) < 2:
            return None

        home = next((team for team in competitors if team["home_away"] == "home"), competitors[0])
        away = next((team for team in competitors if team["home_away"] == "away"), competitors[1])
        competition = espn_competition_name(item, league_slug)
        entity_ids = football_entity_ids(home["name"], away["name"], competition)
        entity_ids.extend(espn_team_entity_ids({home["id"], away["id"]}))
        entity_ids.extend(self._bound_team_entity_ids({home["id"], away["id"]}))
        entity_ids.extend(espn_competition_entity_ids(league_slug))
        entity_ids.extend([entity_id for entity_id, slug in self.competition_entities.items() if slug == league_slug])
        entity_ids = list(dict.fromkeys(entity_ids))

        return Event(
            id=f"football-espn-{league_slug}-{event_id}",
            title=f"{home['name']} vs {away['name']}",
            sport=Sport.FOOTBALL,
            starts_at=starts_at,
            status=espn_status(item, starts_at),
            entity_ids=entity_ids,
            source="espn",
            competition=competition,
            result_summary=espn_score_summary(item, home, away),
            importance=football_importance(entity_ids),
        )

    def _bound_team_entity_ids(self, team_ids: set[str]) -> list[str]:
        return [entity_id for entity_id, values in self.team_ids.items() if team_ids & set(values)]


def month_keys(start: datetime, end: datetime) -> list[str]:
    output: list[str] = []
    year = start.year
    month = start.month
    while (year, month) <= (end.year, end.month):
        output.append(f"{year}{month:02d}")
        month += 1
        if month > 12:
            year += 1
            month = 1
    return output


def espn_competitors(item: dict) -> list[dict]:
    competitions = item.get("competitions") or []
    competitors = competitions[0].get("competitors") if competitions else []
    output: list[dict] = []
    for competitor in competitors or []:
        team = competitor.get("team") or {}
        output.append(
            {
                "id": str(team.get("id") or ""),
                "name": str(team.get("displayName") or team.get("name") or "-"),
                "home_away": str(competitor.get("homeAway") or ""),
                "score": competitor.get("score"),
            }
        )
    return output


def espn_event_team_ids(item: dict) -> set[str]:
    return {team["id"] for team in espn_competitors(item) if team["id"]}


def espn_team_entity_ids(team_ids: set[str]) -> list[str]:
    entities: list[str] = []
    for entity_id, config in ESPN_FOOTBALL_TEAMS.items():
        if team_ids & set(config.get("ids", set())):
            entities.append(entity_id)
    return entities


def espn_competition_entity_ids(league_slug: str) -> list[str]:
    return [entity_id for entity_id, slug in ESPN_FOOTBALL_COMPETITIONS.items() if slug == league_slug]


def espn_competition_name(item: dict, league_slug: str) -> str:
    leagues = item.get("competitions") or []
    if leagues:
        notes = leagues[0].get("notes") or []
        if notes and notes[0].get("headline"):
            return str(notes[0]["headline"])
    fallback = {
        "esp.1": "Spanish LALIGA",
        "uefa.champions": "UEFA Champions League",
        "uefa.nations": "UEFA Nations League",
        "fifa.world": "FIFA World Cup",
        "uefa.euro": "UEFA European Championship",
    }
    return fallback.get(league_slug, league_slug)


def espn_status(item: dict, starts_at: datetime) -> EventStatus:
    status = item.get("status", {}).get("type", {})
    state = status.get("state")
    completed = bool(status.get("completed"))
    if completed or state == "post":
        return EventStatus.PAST
    if state == "in":
        return EventStatus.LIVE
    if starts_at <= datetime.now(timezone.utc):
        return EventStatus.DELAYED
    return EventStatus.UPCOMING


def espn_score_summary(item: dict, home: dict, away: dict) -> str | None:
    status = item.get("status", {}).get("type", {})
    if not status.get("completed"):
        return None
    if home.get("score") is None or away.get("score") is None:
        return None
    return f"{home['name']} {home['score']}-{away['score']} {away['name']}"


def event_in_range(event: Event, start: datetime, end: datetime) -> bool:
    if event.starts_at is None:
        return False
    starts_at = event.starts_at.astimezone(timezone.utc)
    return start <= starts_at < end


def dedupe_events(events: list[Event]) -> list[Event]:
    unique: dict[str, Event] = {}
    for event in events:
        unique[event.id] = event
    return list(unique.values())
