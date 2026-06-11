from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .base import EventProvider
from ..models import Event, EventStatus, Sport


FOOTBALL_CACHE_TTL = timedelta(hours=6)
_RATE_LIMIT_UNTIL: datetime | None = None


class FootballDataProvider(EventProvider):
    def __init__(
        self,
        token: str | None = None,
        competition_entities: dict[str, str] | None = None,
        team_entities: dict[str, int] | None = None,
        team_queries: dict[str, list[str]] | None = None,
        url: str = "https://api.football-data.org/v4/matches",
        team_url: str = "https://api.football-data.org/v4/teams/{team_id}/matches",
        teams_url: str = "https://api.football-data.org/v4/teams",
    ) -> None:
        self.token = token or os.getenv("FOOTBALL_DATA_TOKEN")
        self.competition_entities = competition_entities or football_competition_entities()
        self.team_entities = team_entities or football_team_entities()
        self.team_queries = team_queries or football_team_queries()
        self.url = url
        self.team_url = team_url
        self.teams_url = teams_url

    def fetch(self, start: datetime | None = None, end: datetime | None = None) -> list[Event]:
        # The free plan is enough for fixtures and delayed schedules. The token
        # stays backend-only and is never sent to the iOS client.
        global _RATE_LIMIT_UNTIL

        if not self.token:
            return []
        start_date = (start.astimezone(timezone.utc) if start else datetime.now(timezone.utc) - timedelta(days=9)).date()
        end_date = (end.astimezone(timezone.utc) if end else datetime.now(timezone.utc) + timedelta(days=30)).date()
        cache_path = football_cache_path(start_date.isoformat(), end_date.isoformat())
        cached = read_football_cache(cache_path, max_age=FOOTBALL_CACHE_TTL)
        if cached is not None:
            return [self._match_to_event(item) for item in cached]

        if _RATE_LIMIT_UNTIL is not None and datetime.now(timezone.utc) < _RATE_LIMIT_UNTIL:
            cached = read_football_cache(cache_path)
            if cached is not None:
                return [self._match_to_event(item) for item in cached]
            raise RuntimeError(f"FootballDataProvider is rate-limited until {_RATE_LIMIT_UNTIL.isoformat()}")

        try:
            matches = self._fetch_followed_matches(start_date, end_date)
        except HTTPError as exc:
            # The free API can rate-limit local development quickly. If we have
            # a previous successful payload, keep the product useful instead of
            # dropping football from the timeline.
            if exc.code == 429:
                _RATE_LIMIT_UNTIL = datetime.now(timezone.utc) + timedelta(seconds=90)
                cached = read_football_cache(cache_path)
                if cached is not None:
                    return [self._match_to_event(item) for item in cached]
            raise

        write_football_cache(cache_path, matches)

        return [self._match_to_event(item) for item in matches]

    def _fetch_followed_matches(self, start_date, end_date) -> list[dict]:
        matches: list[dict] = []

        # Team feeds cover "all matches for this team", which is the right
        # product model for Barcelona and Ukraine NT. Competition feeds cover
        # standalone interests such as UCL, World Cup, and Euro.
        for entity_id, team_id in self.resolved_team_entities().items():
            matches.extend(self._fetch_team_matches(team_id, start_date, end_date, entity_id))

        if self.competition_entities:
            matches.extend(self._fetch_competition_matches(start_date, end_date))

        return dedupe_matches(matches)

    def resolved_team_entities(self) -> dict[str, int]:
        resolved = dict(self.team_entities)
        missing = {entity_id: queries for entity_id, queries in self.team_queries.items() if entity_id not in resolved}
        if not missing:
            return resolved

        cached = read_team_id_cache()
        for entity_id in list(missing):
            if entity_id in cached:
                resolved[entity_id] = cached[entity_id]
                missing.pop(entity_id)

        if not missing:
            return resolved

        teams = self._fetch_all_teams()
        for entity_id, queries in missing.items():
            team_id = find_team_id(teams, queries)
            if team_id is not None:
                resolved[entity_id] = team_id
                cached[entity_id] = team_id

        write_team_id_cache(cached)
        return resolved

    def _fetch_all_teams(self) -> list[dict]:
        teams: list[dict] = []
        offset = 0
        limit = 500
        while True:
            query = urlencode({"limit": str(limit), "offset": str(offset)})
            request = Request(
                f"{self.teams_url}?{query}",
                headers={"X-Auth-Token": self.token},
            )
            with urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            chunk = payload.get("teams", [])
            teams.extend(chunk)
            if len(chunk) < limit:
                break
            offset += limit
        return teams

    def _fetch_team_matches(self, team_id: int, start_date, end_date, entity_id: str) -> list[dict]:
        query = urlencode(
            {
                "dateFrom": start_date.isoformat(),
                "dateTo": end_date.isoformat(),
                "limit": "100",
            }
        )
        request = Request(
            f"{self.team_url.format(team_id=team_id)}?{query}",
            headers={"X-Auth-Token": self.token},
        )
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        matches = payload.get("matches", [])
        for match in matches:
            match.setdefault("_matchnest_entity_ids", []).append(entity_id)
        return matches

    def _fetch_competition_matches(self, start_date, end_date) -> list[dict]:
        matches: list[dict] = []
        current = start_date

        # football-data.org limits the generic matches endpoint to short date
        # windows. Fetching chunks keeps broad followed tournaments usable.
        while current <= end_date:
            chunk_end = min(current + timedelta(days=9), end_date)
            query = urlencode(
                {
                    "competitions": ",".join(self.competition_entities.values()),
                    "dateFrom": current.isoformat(),
                    "dateTo": chunk_end.isoformat(),
                }
            )
            request = Request(
                f"{self.url}?{query}",
                headers={"X-Auth-Token": self.token},
            )
            with urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            matches.extend(payload.get("matches", []))
            current = chunk_end + timedelta(days=1)
        return matches

    def _match_to_event(self, item: dict) -> Event:
        home = item.get("homeTeam", {}).get("name", "Home")
        away = item.get("awayTeam", {}).get("name", "Away")
        competition = item.get("competition", {}).get("name")
        entity_ids = football_entity_ids(home, away, competition)
        entity_ids.extend(item.get("_matchnest_entity_ids", []))
        entity_ids = list(dict.fromkeys(entity_ids))
        starts_at = datetime.fromisoformat(item["utcDate"].replace("Z", "+00:00")).astimezone(timezone.utc)

        return Event(
            id=f"football-{item.get('id')}",
            title=f"{home} vs {away}",
            sport=Sport.FOOTBALL,
            starts_at=starts_at,
            status=football_status(item.get("status"), starts_at),
            entity_ids=entity_ids,
            source="football-data",
            competition=competition,
            result_summary=score_summary(item, home, away),
            importance=football_importance(entity_ids),
        )


def football_entity_ids(home: str, away: str, competition: str | None) -> list[str]:
    entities: list[str] = []
    for entity_id, aliases in football_team_aliases().items():
        if team_name_matches(home, aliases) or team_name_matches(away, aliases):
            entities.append(entity_id)

    competition_name = (competition or "").lower()
    if "champions league" in competition_name:
        entities.append("ucl")
    if "world cup" in competition_name:
        entities.append("world_cup")
    if "euro" in competition_name:
        entities.append("euro")
    return entities or ["football_explore"]


def football_status(status: str | None, starts_at: datetime) -> EventStatus:
    if status in {"IN_PLAY", "PAUSED"}:
        return EventStatus.LIVE
    if status == "FINISHED":
        return EventStatus.PAST
    if starts_at <= datetime.now(timezone.utc):
        return EventStatus.PAST
    return EventStatus.UPCOMING


def score_summary(item: dict, home: str, away: str) -> str | None:
    # Past scores are generated here, then stripped by serialize_event when
    # spoiler mode is active.
    if item.get("status") != "FINISHED":
        return None
    full_time = item.get("score", {}).get("fullTime", {})
    home_score = full_time.get("home")
    away_score = full_time.get("away")
    if home_score is None or away_score is None:
        return None
    return f"{home} {home_score}-{away_score} {away}"


def football_importance(entity_ids: list[str]) -> int:
    if "barcelona" in entity_ids or "ukraine_nt" in entity_ids:
        return 90
    if any(item in entity_ids for item in ["ucl", "world_cup", "euro"]):
        return 75
    return 45


def football_competition_entities() -> dict[str, str]:
    # These are the free/core football-data competition codes MatchNest follows
    # as standalone tournaments. Team feeds are fetched separately.
    return {
        "ucl": "CL",
        "world_cup": "WC",
        "euro": "EC",
    }


def football_team_entities() -> dict[str, int]:
    # Team IDs are provider-specific. Keep them configurable so the product can
    # be corrected without code changes if football-data changes an identifier.
    values = parse_entity_id_map(os.getenv("FOOTBALL_DATA_TEAM_IDS", ""))
    return values


def football_team_queries() -> dict[str, list[str]]:
    return parse_entity_query_map(os.getenv("FOOTBALL_DATA_TEAM_QUERIES", ""))


def parse_entity_id_map(value: str) -> dict[str, int]:
    output: dict[str, int] = {}
    for item in value.split(","):
        if ":" not in item:
            continue
        entity_id, raw_id = item.split(":", 1)
        try:
            output[entity_id.strip()] = int(raw_id.strip())
        except ValueError:
            continue
    return output


def parse_entity_query_map(value: str) -> dict[str, list[str]]:
    output: dict[str, list[str]] = {}
    for item in value.split(","):
        if ":" not in item:
            continue
        entity_id, raw_queries = item.split(":", 1)
        queries = [query.strip() for query in raw_queries.split("|") if query.strip()]
        if queries:
            output[entity_id.strip()] = queries
    return output


def find_team_id(teams: list[dict], queries: list[str]) -> int | None:
    normalized_queries = [normalize_team_name(query) for query in queries]
    for team in teams:
        names = [
            team.get("name"),
            team.get("shortName"),
            team.get("tla"),
        ]
        normalized_names = [normalize_team_name(name) for name in names if name]
        if any(query == name for query in normalized_queries for name in normalized_names):
            return team.get("id")
    for team in teams:
        names = [
            team.get("name"),
            team.get("shortName"),
        ]
        normalized_names = [normalize_team_name(name) for name in names if name]
        if any(query in name for query in normalized_queries for name in normalized_names):
            return team.get("id")
    return None


def normalize_team_name(value: str) -> str:
    return " ".join(value.lower().replace(".", "").split())


def football_team_aliases() -> dict[str, list[str]]:
    from ..storage import list_entity_records

    aliases: dict[str, list[str]] = {}
    for entity_id, record in list_entity_records().items():
        entity = record.entity
        if entity.sport != Sport.FOOTBALL or entity.kind.value != "team":
            continue
        values = [entity.name, *record.aliases]
        if entity.name.endswith(" NT"):
            values.append(entity.name.removesuffix(" NT"))
        aliases[entity_id] = values
    return aliases


def team_name_matches(provider_name: str, aliases: list[str]) -> bool:
    normalized_provider_name = normalize_team_name(provider_name)
    return normalized_provider_name in {normalize_team_name(alias) for alias in aliases}


def dedupe_matches(matches: list[dict]) -> list[dict]:
    deduped: dict[str, dict] = {}
    for match in matches:
        key = str(match.get("id"))
        if key in deduped:
            existing = deduped[key]
            existing_ids = existing.setdefault("_matchnest_entity_ids", [])
            for entity_id in match.get("_matchnest_entity_ids", []):
                if entity_id not in existing_ids:
                    existing_ids.append(entity_id)
        else:
            deduped[key] = match
    return list(deduped.values())


def football_cache_path(start: str, end: str) -> Path:
    return Path(__file__).resolve().parents[2] / ".cache" / f"football-data-matches-{start}-{end}.json"


def team_id_cache_path() -> Path:
    return Path(__file__).resolve().parents[2] / ".cache" / "football-data-team-ids.json"


def read_team_id_cache() -> dict[str, int]:
    path = team_id_cache_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {key: int(value) for key, value in payload.items() if str(value).isdigit()}


def write_team_id_cache(values: dict[str, int]) -> None:
    path = team_id_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")


def read_football_cache(path: Path, max_age: timedelta | None = None) -> list[dict] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if max_age is not None:
        fetched_at = parse_cached_datetime(payload.get("fetched_at"))
        if fetched_at is None or datetime.now(timezone.utc) - fetched_at > max_age:
            return None
    matches = payload.get("matches")
    return matches if isinstance(matches, list) else None


def parse_cached_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    except ValueError:
        return None


def write_football_cache(path: Path, matches: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "matches": matches,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
