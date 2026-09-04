from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from urllib.request import Request
from .http_cache import cached_urlopen as urlopen

from ..storage import get_cached_provider_payload, upsert_provider_payload_cache


HLTV_RANKING_URL = "https://www.hltv.org/ranking/teams"
HLTV_CACHE_TTL = timedelta(hours=12)
HLTV_CACHE_PATH = Path(__file__).resolve().parents[2] / ".cache" / "hltv-ranking.json"
HLTV_PROVIDER = "hltv"
HLTV_CACHE_KEY = "team-ranking:current"


@dataclass(frozen=True)
class HltvTeamRank:
    name: str
    rank: int
    points: int
    source_url: str
    fetched_at: str

    @property
    def tier(self) -> str:
        return rank_based_tier(self.rank)


def hltv_rankings() -> dict[str, HltvTeamRank]:
    db_cached = read_hltv_db_cache()
    if db_cached:
        return db_cached
    cached = read_hltv_cache()
    if cached:
        write_hltv_db_cache(cached)
        return cached
    try:
        request = Request(
            HLTV_RANKING_URL,
            headers={
                "Accept": "text/html",
                "User-Agent": "Mozilla/5.0 MatchNest personal schedule app",
            },
        )
        with urlopen(request, timeout=20) as response:
            source_url = response.geturl()
            html = response.read().decode("utf-8", errors="replace")
        rankings = parse_hltv_rankings(html, source_url)
        if rankings:
            write_hltv_cache(rankings)
            write_hltv_db_cache(rankings)
            return rankings
    except Exception:
        stale = read_hltv_db_cache(ignore_ttl=True) or read_hltv_cache(ignore_ttl=True)
        return stale or {}
    return read_hltv_db_cache(ignore_ttl=True) or read_hltv_cache(ignore_ttl=True) or {}


def find_hltv_team_rank(team_name: str, rankings: dict[str, HltvTeamRank] | None = None) -> HltvTeamRank | None:
    active_rankings = rankings if rankings is not None else hltv_rankings()
    keys = team_lookup_keys(team_name)
    for key in keys:
        if key in active_rankings:
            return active_rankings[key]
    return None


def parse_hltv_rankings(html: str, source_url: str) -> dict[str, HltvTeamRank]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    teams: dict[str, HltvTeamRank] = {}
    for block in re.findall(r'<div class="ranked-team standard-box">(?P<block>.*?)(?=<div class="ranked-team standard-box">|$)', html, re.S):
        rank_match = re.search(r'<span class="position wide-position">#(?P<rank>\d+)</span>', block)
        name_match = re.search(r'<span class="name">(?P<name>.*?)</span>', block)
        points_match = re.search(r'<span class="points">\((?P<points>\d+)', block)
        if not rank_match or not name_match or not points_match:
            continue
        team = HltvTeamRank(
            name=clean_html_text(name_match.group("name")),
            rank=int(rank_match.group("rank")),
            points=int(points_match.group("points")),
            source_url=source_url,
            fetched_at=fetched_at,
        )
        for key in team_lookup_keys(team.name):
            teams.setdefault(key, team)
    return teams


def team_lookup_keys(team_name: str) -> list[str]:
    normalized = normalize_team_name(team_name)
    aliases = {
        "navi": "natus vincere",
        "natus vincere": "natus vincere",
        "themongolz": "the mongolz",
        "the mongolz": "the mongolz",
        "team falcons": "falcons",
        "falcons": "falcons",
        "furia esports": "furia",
        "furia": "furia",
        "g2 esports": "g2",
        "g2": "g2",
    }
    keys = [normalized]
    if normalized in aliases:
        keys.append(aliases[normalized])
    compact = normalized.replace(" ", "")
    if compact in aliases:
        keys.append(aliases[compact])
    return list(dict.fromkeys(keys))


def normalize_team_name(value: str) -> str:
    cleaned = re.sub(r"\besports\b", "", value, flags=re.I)
    cleaned = re.sub(r"\bteam\b", "team", cleaned, flags=re.I)
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned.lower()).strip()
    return re.sub(r"\s+", " ", cleaned)


def rank_based_tier(rank: int) -> str:
    if rank <= 10:
        return "T1"
    if rank <= 30:
        return "T2"
    return "T3"


def clean_html_text(value: str) -> str:
    return unescape(re.sub(r"<.*?>", "", value)).strip()


def read_hltv_cache(ignore_ttl: bool = False) -> dict[str, HltvTeamRank]:
    if not HLTV_CACHE_PATH.exists():
        return {}
    try:
        payload = json.loads(HLTV_CACHE_PATH.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(payload["fetched_at"]).astimezone(timezone.utc)
        if not ignore_ttl and datetime.now(timezone.utc) - fetched_at > HLTV_CACHE_TTL:
            return {}
        teams = {
            key: HltvTeamRank(
                name=value["name"],
                rank=int(value["rank"]),
                points=int(value["points"]),
                source_url=value["source_url"],
                fetched_at=value["fetched_at"],
            )
            for key, value in payload.get("teams", {}).items()
        }
        return teams
    except Exception:
        return {}


def write_hltv_cache(rankings: dict[str, HltvTeamRank]) -> None:
    HLTV_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "teams": {key: asdict(value) for key, value in rankings.items()},
    }
    HLTV_CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def read_hltv_db_cache(ignore_ttl: bool = False) -> dict[str, HltvTeamRank]:
    payload = get_cached_provider_payload(HLTV_PROVIDER, HLTV_CACHE_KEY)
    if not isinstance(payload, dict):
        return {}
    try:
        fetched_at = datetime.fromisoformat(payload["fetched_at"]).astimezone(timezone.utc)
        if not ignore_ttl and datetime.now(timezone.utc) - fetched_at > HLTV_CACHE_TTL:
            return {}
        return {
            key: HltvTeamRank(
                name=value["name"],
                rank=int(value["rank"]),
                points=int(value["points"]),
                source_url=value["source_url"],
                fetched_at=value["fetched_at"],
            )
            for key, value in payload.get("teams", {}).items()
        }
    except Exception:
        return {}


def write_hltv_db_cache(rankings: dict[str, HltvTeamRank]) -> None:
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "teams": {key: asdict(value) for key, value in rankings.items()},
    }
    upsert_provider_payload_cache(HLTV_PROVIDER, HLTV_CACHE_KEY, payload)
