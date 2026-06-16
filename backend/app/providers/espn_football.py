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

    def details(self, event_id: str, event: Event | None = None) -> dict | None:
        parsed = parse_espn_event_id(event_id)
        if parsed is None:
            return None
        league_slug, espn_event_id = parsed
        month_key = event.starts_at.astimezone(timezone.utc).strftime("%Y%m") if event and event.starts_at else datetime.now(timezone.utc).strftime("%Y%m")
        item = next((entry for entry in self._scoreboard(league_slug, month_key) if str(entry.get("id")) == espn_event_id), None)
        if item is None:
            return None
        return espn_details_payload(event_id, item, league_slug, self._summary(league_slug, espn_event_id))

    def _summary(self, league_slug: str, espn_event_id: str) -> dict:
        url = f"{self.base_url}/{league_slug}/summary?{urlencode({'event': espn_event_id})}"
        cache_key = f"summary:{league_slug}:{espn_event_id}"
        request = Request(url, headers={"User-Agent": "MatchNest personal schedule app", "Accept": "application/json"})
        try:
            with urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            cached = get_cached_provider_payload("espn-football", cache_key)
            return cached if isinstance(cached, dict) else {}
        upsert_provider_payload_cache("espn-football", cache_key, payload)
        return payload if isinstance(payload, dict) else {}

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


def parse_espn_event_id(event_id: str) -> tuple[str, str] | None:
    prefix = "football-espn-"
    if not event_id.startswith(prefix):
        return None
    tail = event_id[len(prefix) :]
    if "-" not in tail:
        return None
    league_slug, espn_event_id = tail.rsplit("-", 1)
    if not league_slug or not espn_event_id:
        return None
    return league_slug, espn_event_id


def espn_details_payload(event_id: str, item: dict, league_slug: str, summary_payload: dict | None = None) -> dict:
    competitors = espn_competitors(item)
    home = next((team for team in competitors if team["home_away"] == "home"), competitors[0] if competitors else {})
    away = next((team for team in competitors if team["home_away"] == "away"), competitors[1] if len(competitors) > 1 else {})
    competition = espn_competition_name(item, league_slug)
    status = item.get("status", {}).get("type", {})
    summary_payload = summary_payload or {}
    competitions = item.get("competitions") or []
    summary_competitions = (summary_payload.get("header") or {}).get("competitions") or []
    competition_payload = summary_competitions[0] if summary_competitions else competitions[0] if competitions else {}
    venue = competition_payload.get("venue") or {}
    facts = [
        {"label": "Competition", "value": competition},
        {"label": "Status", "value": str(status.get("description") or status.get("detail") or status.get("state") or "-")},
    ]
    if venue.get("fullName"):
        facts.append({"label": "Venue", "value": str(venue["fullName"])})
    if item.get("date"):
        facts.append({"label": "Kickoff", "value": str(item["date"])})

    sections = [
        {
            "title": "Teams",
            "columns": ["Side", "Team", "Score"],
            "rows": [
                [str(home.get("home_away") or "home"), str(home.get("name") or "-"), str(home.get("score") or "-")],
                [str(away.get("home_away") or "away"), str(away.get("name") or "-"), str(away.get("score") or "-")],
            ],
        }
    ]
    team_lookup = espn_team_lookup(competition_payload)
    lineup_rows = espn_lineup_rows(competition_payload)
    if lineup_rows:
        sections.append(
            {
                "title": "Lineups",
                "columns": ["Team", "Role", "No", "Pos", "Player"],
                "rows": lineup_rows,
            }
        )

    boxscore_rows = espn_boxscore_player_rows(summary_payload)
    if boxscore_rows:
        sections.append(
            {
                "title": "Player statistics",
                "columns": ["Team", "Group", "No", "Pos", "Player", "Stats"],
                "rows": boxscore_rows,
            }
        )

    details_rows = espn_match_detail_rows(competition_payload, summary_payload, team_lookup)
    if details_rows:
        sections.append(
            {
                "title": "Match events",
                "columns": ["Time", "Team", "Type", "Player", "Assist", "Details", "Score"],
                "rows": details_rows,
            }
        )
    else:
        sections.append(
            {
                "title": "Match events",
                "columns": ["Info"],
                "rows": [["Detailed match events are not available from the current ESPN payload yet."]],
            }
        )

    summary = espn_score_summary(item, home, away) or f"{home.get('name', 'Home')} vs {away.get('name', 'Away')}"
    return {
        "event_id": event_id,
        "sport": "football",
        "source": "espn",
        "summary": summary,
        "facts": facts,
        "sections": sections,
    }


def espn_match_detail_rows(competition_payload: dict, summary_payload: dict, team_lookup: dict[str, str]) -> list[list[str]]:
    rows: list[list[str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for detail in list(competition_payload.get("details") or []) + list(summary_payload.get("plays") or []):
        if not isinstance(detail, dict):
            continue
        time_value = espn_event_time(detail)
        event_type = espn_event_type(detail)
        player = espn_primary_player(detail)
        assist = espn_assist_player(detail)
        details = str(detail.get("text") or detail.get("headline") or detail.get("displayText") or "-")
        score = espn_event_score(detail)
        key = (time_value, event_type, player, details)
        if key in seen:
            continue
        seen.add(key)
        rows.append([time_value, espn_detail_team(detail, team_lookup), event_type, player, assist, details, score])
    return rows


def espn_team_lookup(competition_payload: dict) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for competitor in competition_payload.get("competitors") or []:
        team = competitor.get("team") or {}
        team_name = str(team.get("displayName") or team.get("name") or "-")
        for key in [team.get("id"), competitor.get("id"), competitor.get("uid")]:
            if key is not None:
                lookup[str(key)] = team_name
    return lookup


def espn_detail_team(detail: dict, team_lookup: dict[str, str]) -> str:
    team = detail.get("team") or {}
    if isinstance(team, dict):
        direct = team.get("displayName") or team.get("name") or team.get("shortDisplayName")
        if direct:
            return str(direct)
        for key in [team.get("id"), team.get("uid")]:
            if key is not None and str(key) in team_lookup:
                return team_lookup[str(key)]
    for key in [detail.get("teamId"), detail.get("team_id"), detail.get("teamUid")]:
        if key is not None and str(key) in team_lookup:
            return team_lookup[str(key)]
    return "-"


def espn_event_time(detail: dict) -> str:
    clock = detail.get("clock") or {}
    if isinstance(clock, dict) and clock.get("displayValue"):
        return str(clock["displayValue"])
    return str(detail.get("time") or detail.get("period", {}).get("displayValue") or "-")


def espn_event_type(detail: dict) -> str:
    value = detail.get("type")
    if isinstance(value, dict):
        return str(value.get("text") or value.get("description") or value.get("name") or "-")
    return str(value or detail.get("scoringType") or "-")


def espn_primary_player(detail: dict) -> str:
    for key in ["athlete", "player", "scorer", "shootingPlayer", "penaltyTaker"]:
        name = espn_person_name(detail.get(key))
        if name != "-":
            return name
    people = detail.get("athletesInvolved") or detail.get("participants") or []
    if isinstance(people, list) and people:
        return espn_person_name(people[0])
    return "-"


def espn_assist_player(detail: dict) -> str:
    for key in ["assist", "assistAthlete", "assister"]:
        name = espn_person_name(detail.get(key))
        if name != "-":
            return name
    people = detail.get("athletesInvolved") or []
    if isinstance(people, list) and len(people) > 1:
        return espn_person_name(people[1])
    return "-"


def espn_person_name(value: object) -> str:
    if isinstance(value, dict):
        nested = value.get("athlete") or value.get("player")
        if isinstance(nested, dict):
            return espn_person_name(nested)
        return str(value.get("displayName") or value.get("fullName") or value.get("name") or value.get("shortName") or "-")
    return "-"

def espn_position_text(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("abbreviation") or value.get("displayName") or value.get("name") or "-")
    return str(value or "-")


def espn_event_score(detail: dict) -> str:
    home = detail.get("homeScore")
    away = detail.get("awayScore")
    if home is not None and away is not None:
        return f"{home}-{away}"
    score = detail.get("score")
    return str(score) if score not in [None, ""] else "-"


def espn_lineup_rows(competition_payload: dict) -> list[list[str]]:
    rows: list[list[str]] = []
    for competitor in competition_payload.get("competitors") or []:
        team = (competitor.get("team") or {}).get("displayName") or (competitor.get("team") or {}).get("name") or "-"
        for role, keys in [("Starter", ["startXI", "starters", "lineup"]), ("Substitute", ["substitutes", "bench"]), ("Roster", ["roster"] )]:
            for key in keys:
                for item in competitor.get(key) or []:
                    player = item.get("player") or item.get("athlete") or item
                    if not isinstance(player, dict):
                        continue
                    rows.append([
                        str(team),
                        role,
                        str(player.get("jersey") or player.get("jerseyNumber") or item.get("jersey") or "-"),
                        espn_position_text(player.get("position")),
                        espn_person_name(player),
                    ])
    return rows


def espn_boxscore_player_rows(summary_payload: dict) -> list[list[str]]:
    rows: list[list[str]] = []
    for team_block in (summary_payload.get("boxscore") or {}).get("players") or []:
        team = (team_block.get("team") or {}).get("displayName") or (team_block.get("team") or {}).get("name") or "-"
        for group in team_block.get("statistics") or []:
            group_name = str(group.get("name") or group.get("displayName") or "Players")
            labels = [str(label) for label in group.get("labels") or []]
            for athlete_block in group.get("athletes") or []:
                athlete = athlete_block.get("athlete") or {}
                stats = athlete_block.get("stats") or []
                rows.append([
                    str(team),
                    group_name,
                    str(athlete.get("jersey") or "-"),
                    espn_position_text(athlete.get("position")),
                    espn_person_name(athlete),
                    espn_labeled_stats(labels, stats),
                ])
    return rows


def espn_labeled_stats(labels: list[str], stats: list[object]) -> str:
    if not stats:
        return "-"
    pairs = []
    for index, value in enumerate(stats):
        label = labels[index] if index < len(labels) else f"S{index + 1}"
        pairs.append(f"{label}: {value}")
    return "; ".join(pairs)


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
