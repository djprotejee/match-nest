from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..storage import (
    get_cached_provider_payload,
    get_event_provider_binding,
    upsert_event_provider_binding,
    upsert_provider_payload_cache,
)


GRID_PROVIDER = "grid"
GRID_API_URL = "https://api.grid.gg/file-download/end-state/grid/series/{series_id}"
GRID_DISCOVERY_TIMEOUT_SECONDS = 12
GRID_DISCOVERY_MIN_SCORE = 78


@dataclass(frozen=True)
class GridSeriesCandidate:
    series_id: str
    title: str
    team_names: list[str]
    tournament: str | None = None
    starts_at: datetime | None = None
    payload: dict | None = None


def parse_grid_series_ids(value: str | None) -> dict[str, str]:
    """Parse manual MatchNest event to GRID series bindings.

    GRID's public tutorial shows an end-state endpoint that needs a GRID
    series id. Until a provider lookup endpoint is wired, MatchNest keeps the
    binding explicit: cs2-12345:2589176,cs2-67890=2589177.
    """
    bindings: dict[str, str] = {}
    if not value:
        return bindings
    for chunk in value.split(","):
        item = chunk.strip()
        if not item:
            continue
        separator = ":" if ":" in item else "=" if "=" in item else None
        if separator is None:
            continue
        event_id, series_id = [part.strip() for part in item.split(separator, 1)]
        if event_id and series_id:
            bindings[event_id] = series_id
    return bindings


def grid_series_id_for_event(event_id: str) -> str | None:
    return get_event_provider_binding(event_id, GRID_PROVIDER, "series_id") or parse_grid_series_ids(os.getenv("GRID_SERIES_IDS")).get(event_id)


def grid_end_state_cache_key(series_id: str) -> str:
    return f"end-state:grid:series:{series_id}"


class GridClient:
    def __init__(self, token: str | None = None, base_url: str = GRID_API_URL) -> None:
        self.token = token or os.getenv("GRID_API_TOKEN", "").strip()
        self.base_url = base_url

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def end_state(self, series_id: str) -> tuple[Any | None, str | None, bool]:
        """Return GRID end-state payload, error text, and whether cache was used."""
        cache_key = grid_end_state_cache_key(series_id)
        cached = get_cached_provider_payload(GRID_PROVIDER, cache_key)
        if not self.configured:
            return cached, "GRID_API_TOKEN is not configured.", cached is not None

        request = Request(
            self.base_url.format(series_id=series_id),
            headers={"Accept": "application/json", "x-api-key": self.token},
        )
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            if cached is not None:
                return cached, f"GRID request failed, showing cached data: {exc}", True
            return None, f"GRID request failed: {exc}", False

        upsert_provider_payload_cache(GRID_PROVIDER, cache_key, payload)
        return payload, None, False

    def series_search(self, url: str) -> tuple[Any | None, str | None, bool]:
        cache_key = f"series-search:{url}"
        cached = get_cached_provider_payload(GRID_PROVIDER, cache_key)
        if not self.configured:
            return cached, "GRID_API_TOKEN is not configured.", cached is not None
        request = Request(
            url,
            headers={"Accept": "application/json", "x-api-key": self.token, "User-Agent": "MatchNest"},
        )
        try:
            with urlopen(request, timeout=GRID_DISCOVERY_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            if cached is not None:
                return cached, f"GRID discovery request failed, showing cached candidates: {exc}", True
            return None, f"GRID discovery request failed: {exc}", False
        if isinstance(payload, (dict, list)):
            upsert_provider_payload_cache(GRID_PROVIDER, cache_key, payload)
        return payload, None, False


def discover_grid_series_for_event(event_id: str, match_item: dict) -> str | None:
    """Try to link a PandaScore match to a GRID series without user-provided ids."""
    existing = grid_series_id_for_event(event_id)
    if existing:
        return existing
    client = GridClient()
    if not client.configured:
        return None

    best: tuple[int, GridSeriesCandidate] | None = None
    for url in grid_discovery_urls(match_item):
        payload, _, _ = client.series_search(url)
        if payload is None:
            continue
        for candidate in extract_grid_series_candidates(payload):
            score = score_grid_candidate(match_item, candidate)
            if best is None or score > best[0]:
                best = (score, candidate)

    if best is None or best[0] < GRID_DISCOVERY_MIN_SCORE:
        return None
    candidate = best[1]
    upsert_event_provider_binding(
        event_id,
        GRID_PROVIDER,
        "series_id",
        candidate.series_id,
        confidence=best[0] / 100,
        metadata={
            "matched_by": "grid-discovery",
            "title": candidate.title,
            "teams": candidate.team_names,
            "tournament": candidate.tournament,
            "starts_at": candidate.starts_at.isoformat() if candidate.starts_at else None,
        },
    )
    return candidate.series_id


def grid_discovery_urls(match_item: dict) -> list[str]:
    explicit = [url.strip() for url in os.getenv("GRID_SERIES_SEARCH_URLS", "").split(",") if url.strip()]
    if explicit:
        return [format_grid_search_url(url, match_item) for url in explicit]

    params = grid_search_params(match_item)
    query = urlencode({key: value for key, value in params.items() if value})
    return [
        f"https://api.grid.gg/series?{query}",
        f"https://api.grid.gg/central-data/series?{query}",
        f"https://api.grid.gg/matches?{query}",
    ]


def format_grid_search_url(url_template: str, match_item: dict) -> str:
    params = grid_search_params(match_item)
    output = url_template
    for key, value in params.items():
        output = output.replace("{" + key + "}", value)
    return output


def grid_search_params(match_item: dict) -> dict[str, str]:
    starts_at = parse_grid_datetime(match_item.get("begin_at"))
    opponents = [str(item.get("opponent", {}).get("name") or "") for item in match_item.get("opponents", [])]
    title = " vs ".join([name for name in opponents if name]) or str(match_item.get("name") or "")
    competition = " ".join(
        str((match_item.get(key) or {}).get("name") or (match_item.get(key) or {}).get("full_name") or "")
        for key in ["league", "serie", "tournament"]
    ).strip()
    params = {
        "title": title,
        "team": opponents[0] if opponents else "",
        "team1": opponents[0] if len(opponents) > 0 else "",
        "team2": opponents[1] if len(opponents) > 1 else "",
        "tournament": competition,
        "game": "cs2",
    }
    if starts_at:
        params["from"] = (starts_at - timedelta(hours=12)).isoformat().replace("+00:00", "Z")
        params["to"] = (starts_at + timedelta(hours=36)).isoformat().replace("+00:00", "Z")
    return params


def grid_cs2_section(event_id: str, match_item: dict | None = None) -> dict | None:
    series_id = discover_grid_series_for_event(event_id, match_item) if match_item else grid_series_id_for_event(event_id)
    if not series_id:
        if os.getenv("GRID_API_TOKEN", "").strip():
            return {
                "title": "GRID discovery",
                "columns": ["State", "Value"],
                "rows": [
                    ["Status", "Automatic search is configured, but no GRID series match has been linked yet."],
                    ["Next step", "MatchNest will keep searching through configured GRID discovery endpoints."],
                ],
            }
        return None

    payload, error, from_cache = GridClient().end_state(series_id)
    rows = [["Series ID", series_id], ["Raw cache", f"{GRID_PROVIDER}:{grid_end_state_cache_key(series_id)}"]]
    if from_cache:
        rows.append(["Cache", "Using cached GRID end-state payload"])
    if error:
        rows.append(["Provider message", error])
    if payload is None:
        return {"title": "GRID", "columns": ["Field", "Value"], "rows": rows}

    rows.extend(grid_payload_overview_rows(payload))
    return {"title": "GRID end-state", "columns": ["Field", "Value"], "rows": rows}


def grid_payload_overview_rows(payload: Any) -> list[list[str]]:
    rows: list[list[str]] = []
    if isinstance(payload, dict):
        rows.append(["Payload", "object"])
        for key in sorted(payload.keys()):
            value = payload.get(key)
            if isinstance(value, list):
                rows.append([str(key), f"{len(value)} items"])
            elif isinstance(value, dict):
                rows.append([str(key), f"{len(value)} fields"])
            elif value is None:
                rows.append([str(key), "-"])
            else:
                rows.append([str(key), trim_value(value)])
    elif isinstance(payload, list):
        rows.append(["Payload", f"{len(payload)} items"])
    else:
        rows.append(["Payload", trim_value(payload)])
    return rows


def trim_value(value: Any) -> str:
    text = str(value)
    return text if len(text) <= 120 else f"{text[:117]}..."


def extract_grid_series_candidates(payload: Any) -> list[GridSeriesCandidate]:
    candidates = []
    for item in walk_dicts(payload):
        series_id = first_value(item, ["series_id", "seriesId", "id"])
        if series_id is None:
            continue
        title = str(first_value(item, ["title", "name", "displayName"]) or "")
        teams = extract_team_names(item)
        starts_at = parse_grid_datetime(first_value(item, ["startTime", "start_time", "startsAt", "scheduledStartTime", "begin_at"]))
        tournament = str(first_value(item, ["tournament", "competition", "league", "event"]) or "") or None
        if not title and not teams:
            continue
        candidates.append(
            GridSeriesCandidate(
                series_id=str(series_id),
                title=title,
                team_names=teams,
                tournament=tournament,
                starts_at=starts_at,
                payload=item,
            )
        )
    return dedupe_grid_candidates(candidates)


def walk_dicts(payload: Any) -> list[dict]:
    output = []
    if isinstance(payload, dict):
        output.append(payload)
        for value in payload.values():
            output.extend(walk_dicts(value))
    elif isinstance(payload, list):
        for item in payload:
            output.extend(walk_dicts(item))
    return output


def first_value(item: dict, keys: list[str]) -> Any:
    for key in keys:
        if key in item and item[key] not in [None, ""]:
            return item[key]
    return None


def extract_team_names(item: dict) -> list[str]:
    names = []
    for key in ["teams", "participants", "competitors", "opponents"]:
        value = item.get(key)
        if not isinstance(value, list):
            continue
        for entry in value:
            if isinstance(entry, dict):
                candidate = first_value(entry, ["name", "displayName", "teamName"])
                if candidate is None and isinstance(entry.get("team"), dict):
                    candidate = first_value(entry["team"], ["name", "displayName", "teamName"])
                if candidate:
                    names.append(str(candidate))
    for key in ["team1", "team2", "homeTeam", "awayTeam"]:
        value = item.get(key)
        if isinstance(value, dict):
            candidate = first_value(value, ["name", "displayName", "teamName"])
            if candidate:
                names.append(str(candidate))
        elif isinstance(value, str):
            names.append(value)
    return list(dict.fromkeys(names))


def dedupe_grid_candidates(candidates: list[GridSeriesCandidate]) -> list[GridSeriesCandidate]:
    output = {}
    for candidate in candidates:
        output.setdefault(candidate.series_id, candidate)
    return list(output.values())


def score_grid_candidate(match_item: dict, candidate: GridSeriesCandidate) -> int:
    score = 0
    panda_teams = [str(item.get("opponent", {}).get("name") or "") for item in match_item.get("opponents", [])]
    if team_overlap_score(panda_teams, candidate.team_names or [candidate.title]) >= 2:
        score += 55
    elif team_overlap_score(panda_teams, candidate.team_names or [candidate.title]) == 1:
        score += 25

    match_start = parse_grid_datetime(match_item.get("begin_at"))
    if match_start and candidate.starts_at:
        delta = abs((match_start - candidate.starts_at).total_seconds())
        if delta <= 2 * 60 * 60:
            score += 25
        elif delta <= 24 * 60 * 60:
            score += 10

    panda_competition = " ".join(
        str((match_item.get(key) or {}).get("name") or (match_item.get(key) or {}).get("full_name") or "")
        for key in ["league", "serie", "tournament"]
    )
    if token_overlap(panda_competition, " ".join([candidate.title, candidate.tournament or ""])) >= 2:
        score += 20
    return min(score, 100)


def team_overlap_score(source_teams: list[str], candidate_teams: list[str]) -> int:
    matched = 0
    candidate_text = " ".join(candidate_teams)
    for team in source_teams:
        if token_overlap(team, candidate_text) >= 1:
            matched += 1
    return matched


def token_overlap(left: str, right: str) -> int:
    left_tokens = meaningful_tokens(left)
    right_tokens = meaningful_tokens(right)
    return len(left_tokens & right_tokens)


def meaningful_tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) >= 2 and token not in {"team", "esports", "gaming"}}


def parse_grid_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        timestamp = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None
    return None
