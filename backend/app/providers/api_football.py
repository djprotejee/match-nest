from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .base import EventProvider
from .football_data import (
    football_entity_ids,
    football_importance,
    normalize_team_name,
    parse_entity_id_map,
)
from ..models import Event, EventStatus, Sport
from ..storage import get_cached_provider_payload, provider_payload_state, upsert_provider_payload_cache

API_FOOTBALL_CACHE_TTL = timedelta(hours=12)
API_FOOTBALL_TEAM_CACHE_TTL = timedelta(days=30)
API_FOOTBALL_DETAILS_CACHE_TTL = timedelta(minutes=10)
API_FOOTBALL_STABLE_DETAILS_CACHE_TTL = timedelta(days=30)


class ApiFootballProvider(EventProvider):
    def __init__(
        self,
        token: str | None = None,
        team_queries: dict[str, list[str]] | None = None,
        team_entities: dict[str, int] | None = None,
        base_url: str = "https://v3.football.api-sports.io",
    ) -> None:
        self.token = token or os.getenv("API_FOOTBALL_TOKEN")
        self.team_queries = team_queries or {}
        self.team_entities = team_entities or api_football_team_entities()
        self.base_url = base_url.rstrip("/")

    def fetch(self, start: datetime | None = None, end: datetime | None = None) -> list[Event]:
        if not self.token:
            return []

        start_date = (start.astimezone(timezone.utc) if start else datetime.now(timezone.utc)).date()
        end_date = (end.astimezone(timezone.utc) if end else datetime.now(timezone.utc)).date()
        events: list[Event] = []

        for entity_id, team_id in self.resolved_team_entities().items():
            for season in api_football_seasons(start_date, end_date):
                params = {
                    "team": str(team_id),
                    "season": str(season),
                    "from": start_date.isoformat(),
                    "to": end_date.isoformat(),
                }
                fixtures = self._cached_request("fixtures", params, API_FOOTBALL_CACHE_TTL).get("response", [])
                for item in fixtures:
                    event = self._fixture_to_event(item, entity_id)
                    if event is not None:
                        events.append(event)

        return dedupe_events(events)

    def resolved_team_entities(self) -> dict[str, int]:
        resolved = dict(self.team_entities)
        for entity_id, queries in self.team_queries.items():
            if entity_id in resolved:
                continue
            team_id = self._find_team_id(queries)
            if team_id is not None:
                resolved[entity_id] = team_id
        return resolved

    def _find_team_id(self, queries: list[str]) -> int | None:
        normalized_queries = {normalize_team_name(query) for query in queries}
        for query in queries:
            try:
                payload = self._cached_request("teams", {"search": query}, API_FOOTBALL_TEAM_CACHE_TTL)
            except HTTPError:
                continue
            candidates = payload.get("response", [])
            preferred = [item for item in candidates if team_matches_query(item, normalized_queries, national=True)]
            fallback = [item for item in candidates if team_matches_query(item, normalized_queries, national=False)]
            for item in preferred or fallback:
                team = item.get("team", {})
                return team.get("id")
        return None

    def _cached_request(self, endpoint: str, params: dict[str, str], max_age: timedelta) -> dict:
        path = api_football_cache_path(endpoint, params)
        cache_key = api_football_cache_key(endpoint, params)
        db_cached = read_fresh_api_football_payload(cache_key, max_age)
        if db_cached is not None:
            return db_cached
        cached = read_api_football_cache(path, max_age)
        if cached is not None:
            return cached
        try:
            payload = self._request(endpoint, params)
        except Exception:
            stale = read_api_football_cache(path, None)
            if stale is None:
                stale = read_api_football_payload(cache_key)
            if stale is not None:
                return stale
            raise
        write_api_football_cache(path, payload)
        upsert_provider_payload_cache("api-football", cache_key, payload)
        return payload

    def _request(self, endpoint: str, params: dict[str, str]) -> dict:
        url = f"{self.base_url}/{endpoint}?{urlencode(params)}"
        request = Request(url, headers={"x-apisports-key": self.token or ""})
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        errors = payload.get("errors")
        if errors:
            raise RuntimeError(f"API-Football {endpoint} error: {errors}")
        return payload

    def _fixture_to_event(self, item: dict, followed_entity_id: str) -> Event | None:
        fixture = item.get("fixture", {})
        fixture_id = fixture.get("id")
        date_value = fixture.get("date")
        if fixture_id is None or not date_value:
            return None

        starts_at = datetime.fromisoformat(date_value.replace("Z", "+00:00")).astimezone(timezone.utc)
        teams = item.get("teams", {})
        home = teams.get("home", {}).get("name") or "Home"
        away = teams.get("away", {}).get("name") or "Away"
        league = item.get("league", {})
        competition = league.get("name")
        entity_ids = football_entity_ids(home, away, competition)
        if followed_entity_id not in entity_ids:
            entity_ids.append(followed_entity_id)
        entity_ids = list(dict.fromkeys(entity_ids))

        return Event(
            id=f"football-apifootball-{fixture_id}",
            title=f"{home} vs {away}",
            sport=Sport.FOOTBALL,
            starts_at=starts_at,
            status=api_football_status(fixture.get("status", {}), starts_at),
            entity_ids=entity_ids,
            source="api-football",
            competition=competition,
            result_summary=api_football_score_summary(item, home, away),
            importance=football_importance(entity_ids),
        )

    def details(self, event_id: str) -> dict | None:
        fixture_id = parse_api_football_event_id(event_id)
        if fixture_id is None or not self.token:
            return None
        return self._fixture_details(event_id, fixture_id)

    def details_for_event(self, event_id: str, event: Event | None) -> dict | None:
        """Resolve rich API-Football details for any football event.

        ESPN and other schedule providers can discover fixtures that API-Football
        also knows about. This resolver avoids asking the user for ids by matching
        the followed team, kickoff day, and team names against API-Football
        fixtures, then reuses the same details/cache pipeline as native
        API-Football events.
        """
        if not self.token:
            return None
        fixture_id = parse_api_football_event_id(event_id)
        if fixture_id is None and event is not None:
            fixture_id = self._find_fixture_id_for_event(event)
        if fixture_id is None:
            return None
        return self._fixture_details(event_id, fixture_id)

    def _fixture_details(self, event_id: str, fixture_id: int) -> dict | None:
        fixture_params = {"id": str(fixture_id)}
        fixture_payload = self._cached_request("fixtures", fixture_params, API_FOOTBALL_DETAILS_CACHE_TTL)
        fixtures = fixture_payload.get("response") or []
        if not fixtures:
            return None
        fixture = fixtures[0]
        stable_ttl = API_FOOTBALL_STABLE_DETAILS_CACHE_TTL if api_football_status(fixture.get("fixture", {}).get("status", {}), datetime.now(timezone.utc)) == EventStatus.PAST else API_FOOTBALL_DETAILS_CACHE_TTL
        detail_requests = {
            "events": ("fixtures/events", {"fixture": str(fixture_id)}),
            "lineups": ("fixtures/lineups", {"fixture": str(fixture_id)}),
            "statistics": ("fixtures/statistics", {"fixture": str(fixture_id)}),
            "players": ("fixtures/players", {"fixture": str(fixture_id)}),
        }
        detail_payloads = {
            name: self._cached_request(endpoint, params, stable_ttl)
            for name, (endpoint, params) in detail_requests.items()
        }
        league = fixture.get("league") or {}
        if league.get("id") and league.get("season"):
            detail_requests["standings"] = ("standings", {"league": str(league["id"]), "season": str(league["season"])})
            endpoint, params = detail_requests["standings"]
            detail_payloads["standings"] = self._cached_request(endpoint, params, API_FOOTBALL_STABLE_DETAILS_CACHE_TTL)
        raw_cache_keys = [api_football_cache_key("fixtures", fixture_params)]
        raw_cache_keys.extend(api_football_cache_key(endpoint, params) for endpoint, params in detail_requests.values())
        return api_football_details_payload(event_id, fixture, detail_payloads, raw_cache_keys)

    def _find_fixture_id_for_event(self, event: Event) -> int | None:
        if event.starts_at is None:
            return None
        team_entities = self.resolved_team_entities()
        followed_team_ids = [team_entities[entity_id] for entity_id in event.entity_ids if entity_id in team_entities]
        if not followed_team_ids:
            return None
        starts_at = event.starts_at.astimezone(timezone.utc)
        start_date = (starts_at - timedelta(days=1)).date()
        end_date = (starts_at + timedelta(days=1)).date()
        best: tuple[int, int] | None = None
        for team_id in followed_team_ids:
            for season in api_football_seasons(start_date, end_date):
                params = {
                    "team": str(team_id),
                    "season": str(season),
                    "from": start_date.isoformat(),
                    "to": end_date.isoformat(),
                }
                for fixture in self._cached_request("fixtures", params, API_FOOTBALL_CACHE_TTL).get("response", []):
                    score = score_api_football_fixture_match(event, fixture)
                    fixture_id = ((fixture.get("fixture") or {}).get("id"))
                    if fixture_id is not None and score > 0 and (best is None or score > best[0]):
                        best = (score, int(fixture_id))
        return best[1] if best and best[0] >= 70 else None


def api_football_team_entities() -> dict[str, int]:
    return parse_entity_id_map(os.getenv("API_FOOTBALL_TEAM_IDS", ""))


def api_football_cache_path(endpoint: str, params: dict[str, str]) -> Path:
    safe = "_".join(f"{key}-{value}" for key, value in sorted(params.items()))
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in safe)
    return Path(__file__).resolve().parents[2] / ".cache" / f"api-football-{endpoint}-{safe}.json"


def api_football_cache_key(endpoint: str, params: dict[str, str]) -> str:
    safe = urlencode(sorted(params.items()))
    return f"{endpoint}:{safe}"


def read_fresh_api_football_payload(cache_key: str, max_age: timedelta) -> dict | None:
    state = provider_payload_state("api-football", cache_key)
    if state is None or datetime.now(timezone.utc) - state.fetched_at > max_age:
        return None
    return state.payload if isinstance(state.payload, dict) else None


def read_api_football_payload(cache_key: str) -> dict | None:
    payload = get_cached_provider_payload("api-football", cache_key)
    return payload if isinstance(payload, dict) else None


def read_api_football_cache(path: Path, max_age: timedelta | None) -> dict | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if max_age is not None:
        cached_at = payload.get("cached_at")
        if not cached_at:
            return None
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(cached_at).astimezone(timezone.utc)
        except ValueError:
            return None
        if age > max_age:
            return None
    return payload.get("payload")


def write_api_football_cache(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"cached_at": datetime.now(timezone.utc).isoformat(), "payload": payload}, ensure_ascii=False),
        encoding="utf-8",
    )


def api_football_seasons(start_date: date, end_date: date) -> list[int]:
    # API-Football fixtures require an explicit season. Calendar ranges can
    # cross a season boundary, so query the possible football seasons involved.
    seasons = {start_date.year, end_date.year}
    if start_date.month <= 6:
        seasons.add(start_date.year - 1)
    if end_date.month <= 6:
        seasons.add(end_date.year - 1)
    return sorted(seasons)


def team_matches_query(item: dict, normalized_queries: set[str], national: bool) -> bool:
    team = item.get("team", {})
    if bool(team.get("national")) != national:
        return False
    names = [
        team.get("name"),
        team.get("code"),
    ]
    normalized_names = {normalize_team_name(name) for name in names if name}
    return bool(normalized_queries & normalized_names)


def api_football_status(status: dict, starts_at: datetime) -> EventStatus:
    short = status.get("short")
    if short in {"1H", "HT", "2H", "ET", "BT", "P", "INT", "LIVE"}:
        return EventStatus.LIVE
    if short in {"FT", "AET", "PEN"}:
        return EventStatus.PAST
    if short in {"TBD"}:
        return EventStatus.TBD
    if short in {"PST", "CANC", "ABD", "AWD", "WO"}:
        return EventStatus.DELAYED
    if starts_at <= datetime.now(timezone.utc):
        return EventStatus.DELAYED if short in {"NS", None} else EventStatus.PAST
    return EventStatus.UPCOMING


def api_football_score_summary(item: dict, home: str, away: str) -> str | None:
    status = item.get("fixture", {}).get("status", {}).get("short")
    if status not in {"FT", "AET", "PEN"}:
        return None
    goals = item.get("goals", {})
    home_score = goals.get("home")
    away_score = goals.get("away")
    if home_score is None or away_score is None:
        return None
    return f"{home} {home_score}-{away_score} {away}"


def parse_api_football_event_id(event_id: str) -> int | None:
    prefix = "football-apifootball-"
    if not event_id.startswith(prefix):
        return None
    try:
        return int(event_id[len(prefix) :])
    except ValueError:
        return None


def score_api_football_fixture_match(event: Event, fixture: dict) -> int:
    fixture_meta = fixture.get("fixture") or {}
    date_value = fixture_meta.get("date")
    if not date_value or event.starts_at is None:
        return 0
    try:
        fixture_start = datetime.fromisoformat(str(date_value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return 0
    delta_hours = abs((fixture_start - event.starts_at.astimezone(timezone.utc)).total_seconds()) / 3600
    if delta_hours > 36:
        return 0
    score = max(0, 36 - int(delta_hours))
    teams = fixture.get("teams") or {}
    names = {
        normalize_team_name(str((teams.get("home") or {}).get("name") or "")),
        normalize_team_name(str((teams.get("away") or {}).get("name") or "")),
    }
    title = normalize_team_name(event.title)
    score += sum(25 for name in names if name and name in title)
    competition = normalize_team_name(event.competition or "")
    fixture_competition = normalize_team_name(str((fixture.get("league") or {}).get("name") or ""))
    if competition and fixture_competition and (competition in fixture_competition or fixture_competition in competition):
        score += 15
    return score


def api_football_details_payload(
    event_id: str,
    fixture: dict,
    detail_payloads: dict[str, dict],
    raw_cache_keys: list[str] | None = None,
) -> dict:
    teams = fixture.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    league = fixture.get("league") or {}
    fixture_meta = fixture.get("fixture") or {}
    status = fixture_meta.get("status") or {}
    score = fixture.get("score") or {}
    goals = fixture.get("goals") or {}
    home_name = str(home.get("name") or "Home")
    away_name = str(away.get("name") or "Away")
    summary = api_football_score_summary(fixture, home_name, away_name) or f"{home_name} vs {away_name}"
    facts = [
        {"label": "Competition", "value": str(league.get("name") or "-")},
        {"label": "Status", "value": str(status.get("long") or status.get("short") or "-")},
        {"label": "Venue", "value": fixture_venue_text(fixture_meta.get("venue") or {})},
        {"label": "Round", "value": str(league.get("round") or "-")},
    ]
    sections = [
        football_score_section(home_name, away_name, goals, score),
        football_events_section(detail_payloads.get("events", {})),
        football_lineups_section(detail_payloads.get("lineups", {})),
        football_team_stats_section(detail_payloads.get("statistics", {})),
        football_player_stats_section(detail_payloads.get("players", {})),
        football_standings_section(detail_payloads.get("standings", {}), [home.get("id"), away.get("id")]),
    ]
    return {
        "event_id": event_id,
        "sport": "football",
        "source": "api-football",
        "summary": summary,
        "facts": facts,
        "sections": [section for section in sections if section is not None],
        "raw_payload_cache_keys": [f"api-football:{key}" for key in raw_cache_keys or []],
    }


def fixture_venue_text(venue: dict) -> str:
    parts = [venue.get("name"), venue.get("city")]
    return ", ".join(str(part) for part in parts if part) or "-"


def football_score_section(home: str, away: str, goals: dict, score: dict) -> dict:
    return {
        "title": "Score",
        "columns": ["Team", "Goals", "Half time", "Full time", "Extra time", "Penalty"],
        "rows": [
            [
                home,
                value_text(goals.get("home")),
                value_text((score.get("halftime") or {}).get("home")),
                value_text((score.get("fulltime") or {}).get("home")),
                value_text((score.get("extratime") or {}).get("home")),
                value_text((score.get("penalty") or {}).get("home")),
            ],
            [
                away,
                value_text(goals.get("away")),
                value_text((score.get("halftime") or {}).get("away")),
                value_text((score.get("fulltime") or {}).get("away")),
                value_text((score.get("extratime") or {}).get("away")),
                value_text((score.get("penalty") or {}).get("away")),
            ],
        ],
    }


def football_events_section(payload: dict) -> dict | None:
    rows = []
    for item in payload.get("response") or []:
        team = item.get("team") or {}
        player = item.get("player") or {}
        assist = item.get("assist") or {}
        time = item.get("time") or {}
        rows.append(
            [
                f"{value_text(time.get('elapsed'))}'",
                str(team.get("name") or "-"),
                str(item.get("type") or "-"),
                str(item.get("detail") or "-"),
                str(player.get("name") or "-"),
                str(assist.get("name") or "-"),
                str(item.get("comments") or "-"),
            ]
        )
    if not rows:
        return {"title": "Match events", "columns": ["Info"], "rows": [["No match events are available from API-Football yet."]]}
    return {"title": "Match events", "columns": ["Time", "Team", "Type", "Detail", "Player", "Assist", "Comment"], "rows": rows}


def football_lineups_section(payload: dict) -> dict | None:
    rows = []
    for team_lineup in payload.get("response") or []:
        team = (team_lineup.get("team") or {}).get("name") or "-"
        formation = team_lineup.get("formation") or "-"
        for player_item in team_lineup.get("startXI") or []:
            player = player_item.get("player") or {}
            rows.append([str(team), "XI", str(formation), value_text(player.get("number")), str(player.get("name") or "-"), str(player.get("pos") or "-")])
        for player_item in team_lineup.get("substitutes") or []:
            player = player_item.get("player") or {}
            rows.append([str(team), "Bench", str(formation), value_text(player.get("number")), str(player.get("name") or "-"), str(player.get("pos") or "-")])
    if not rows:
        return {"title": "Lineups", "columns": ["Info"], "rows": [["Lineups are not available from API-Football yet."]]}
    return {"title": "Lineups", "columns": ["Team", "Role", "Formation", "No", "Player", "Pos"], "rows": rows}


def football_team_stats_section(payload: dict) -> dict | None:
    response = payload.get("response") or []
    if len(response) < 2:
        return {"title": "Team statistics", "columns": ["Info"], "rows": [["Team statistics are not available from API-Football yet."]]}
    teams = []
    for item in response[:2]:
        team = item.get("team") or {}
        stats = {stat.get("type"): stat.get("value") for stat in item.get("statistics") or []}
        teams.append((str(team.get("name") or "-"), stats))
    stat_names = sorted(set(teams[0][1]) | set(teams[1][1]))
    rows = [[name, value_text(teams[0][1].get(name)), value_text(teams[1][1].get(name))] for name in stat_names]
    return {"title": "Team statistics", "columns": ["Metric", teams[0][0], teams[1][0]], "rows": rows}


def football_player_stats_section(payload: dict) -> dict | None:
    rows = []
    for team_item in payload.get("response") or []:
        team = (team_item.get("team") or {}).get("name") or "-"
        for player_item in team_item.get("players") or []:
            player = player_item.get("player") or {}
            stats = (player_item.get("statistics") or [{}])[0]
            games = stats.get("games") or {}
            goals = stats.get("goals") or {}
            passes = stats.get("passes") or {}
            duels = stats.get("duels") or {}
            rows.append(
                [
                    str(team),
                    value_text(player.get("number")),
                    str(player.get("name") or "-"),
                    str(games.get("position") or "-"),
                    value_text(games.get("minutes")),
                    value_text(games.get("rating")),
                    value_text(goals.get("total")),
                    value_text(goals.get("assists")),
                    value_text(passes.get("key")),
                    value_text(duels.get("won")),
                ]
            )
    if not rows:
        return {"title": "Player statistics", "columns": ["Info"], "rows": [["Player ratings and statistics are not available from API-Football yet."]]}
    return {"title": "Player statistics", "columns": ["Team", "No", "Player", "Pos", "Min", "Rating", "G", "A", "Key passes", "Duels won"], "rows": rows}


def football_standings_section(payload: dict, team_ids: list[object]) -> dict | None:
    leagues = payload.get("response") or []
    standings = (((leagues[0] if leagues else {}).get("league") or {}).get("standings") or [[]])[0]
    selected_ids = {str(team_id) for team_id in team_ids if team_id is not None}
    rows = []
    for item in standings:
        team = item.get("team") or {}
        if selected_ids and str(team.get("id")) not in selected_ids:
            continue
        all_stats = item.get("all") or {}
        goals = all_stats.get("goals") or {}
        rows.append(
            [
                value_text(item.get("rank")),
                str(team.get("name") or "-"),
                value_text(item.get("points")),
                value_text(all_stats.get("played")),
                value_text(all_stats.get("win")),
                value_text(all_stats.get("draw")),
                value_text(all_stats.get("lose")),
                f"{value_text(goals.get('for'))}-{value_text(goals.get('against'))}",
                value_text(item.get("description")),
            ]
        )
    if not rows:
        return {"title": "Standings snapshot", "columns": ["Info"], "rows": [["Standings are not available for this fixture from API-Football yet."]]}
    return {"title": "Standings snapshot", "columns": ["Rank", "Team", "Pts", "P", "W", "D", "L", "Goals", "Note"], "rows": rows}


def value_text(value: object) -> str:
    return "-" if value is None else str(value)


def dedupe_events(events: list[Event]) -> list[Event]:
    unique: dict[str, Event] = {}
    for event in events:
        unique[event.id] = event
    return list(unique.values())
