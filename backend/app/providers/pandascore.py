from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .base import EventProvider
from .grid import grid_cs2_section
from .hltv import find_hltv_team_rank, hltv_rankings
from ..models import Event, EventStatus, Sport
from ..storage import get_cached_provider_payload, upsert_provider_payload_cache


class PandaScoreCS2Provider(EventProvider):
    def __init__(
        self,
        token: str | None = None,
        base_url: str = "https://api.pandascore.co/csgo/matches",
    ) -> None:
        self.token = token or os.getenv("PANDASCORE_TOKEN")
        self.base_url = base_url

    def fetch(self, start: datetime | None = None, end: datetime | None = None) -> list[Event]:
        # PandaScore marks upcoming, past, and running CS match endpoints as
        # available to all plans. Fetching them separately also avoids pulling
        # unnecessary match details that may belong to paid tiers. Date-bounded
        # ranges are important for past matches because the plain past endpoint
        # can return cancelled TBD matches before finished matches with scores.
        if not self.token:
            return []
        payload = dedupe_matches(
            [
                item
                for bucket in ["running", "upcoming", "past"]
                for item in self._fetch_bucket(bucket, pages=pages_for_bucket(bucket, start, end), start=start, end=end)
            ]
        )
        events = [self._match_to_event(item) for item in payload]
        return [event for event in events if event_in_range(event, start, end)]

    def _fetch_bucket(
        self,
        bucket: str,
        pages: int = 1,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict]:
        sort = "begin_at" if start and end else "-begin_at" if bucket == "past" else "begin_at"
        output: list[dict] = []
        for page in range(1, pages + 1):
            params = {"sort": sort, "per_page": "100", "page": str(page)}
            if start and end:
                params["range[begin_at]"] = (
                    f"{start.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')},"
                    f"{end.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')}"
                )
            query = urlencode(params)
            cache_key = f"matches:{bucket}:{query}"
            cached_page = get_cached_provider_payload("pandascore", cache_key)
            if can_use_stable_pandascore_cache(bucket, end) and isinstance(cached_page, list):
                page_items = cached_page
            else:
                page_items = self._fetch_bucket_page(bucket, query, cache_key)
                if page_items is None and isinstance(cached_page, list):
                    page_items = cached_page
                elif page_items is None:
                    break
            if not page_items:
                break
            output.extend(page_items)
            if len(page_items) < 100:
                break
        return output

    def _fetch_bucket_page(self, bucket: str, query: str, cache_key: str) -> list[dict] | None:
        request = Request(
            f"{self.base_url}/{bucket}?{query}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
        )
        try:
            with urlopen(request, timeout=20) as response:
                page_items = json.loads(response.read().decode("utf-8"))
        except Exception:
            return None
        if isinstance(page_items, list):
            upsert_provider_payload_cache("pandascore", cache_key, page_items)
            return page_items
        return None

    def _match_to_event(self, item: dict) -> Event:
        opponents = [opponent.get("opponent", {}).get("name", "") for opponent in item.get("opponents", [])]
        title = " vs ".join([name for name in opponents if name]) or item.get("name", "CS2 match")
        league = item.get("league", {}).get("name")
        serie = item.get("serie", {}).get("full_name")
        competition = " - ".join([part for part in [league, serie] if part]) or league or serie
        starts_at = parse_pandascore_datetime(item.get("begin_at"))
        entity_ids = cs2_entity_ids(title, competition)
        entity_ids.extend(pandascore_bound_team_entity_ids(item))
        entity_ids = list(dict.fromkeys(entity_ids))

        return Event(
            id=f"cs2-{item.get('id')}",
            title=title,
            sport=Sport.CS2,
            starts_at=starts_at,
            status=cs2_status(item.get("status"), starts_at),
            entity_ids=entity_ids,
            source="pandascore",
            competition=competition,
            result_summary=cs2_score_summary(item, title),
            importance=cs2_importance(entity_ids),
        )

    def details(self, event_id: str, stored_event: Event | None = None) -> dict | None:
        match_id = parse_cs2_event_id(event_id)
        if match_id is None or not self.token:
            return None
        item = self._find_match_item(match_id, stored_event)
        if item is None:
            return None
        return cs2_match_details(event_id, item)

    def _find_match_item(self, match_id: int, stored_event: Event | None) -> dict | None:
        if stored_event and stored_event.starts_at:
            start = stored_event.starts_at.astimezone(timezone.utc) - timedelta(hours=12)
            end = stored_event.starts_at.astimezone(timezone.utc) + timedelta(hours=36)
            buckets = ["running", "upcoming", "past"]
            for bucket in buckets:
                for item in self._fetch_bucket(bucket, pages=pages_for_bucket(bucket, start, end), start=start, end=end):
                    if item.get("id") == match_id:
                        return item

        now = datetime.now(timezone.utc)
        start = now - timedelta(days=14)
        end = now + timedelta(days=14)
        for bucket in ["running", "upcoming", "past"]:
            for item in self._fetch_bucket(bucket, pages=pages_for_bucket(bucket, start, end), start=start, end=end):
                if item.get("id") == match_id:
                    return item
        return None


def can_use_stable_pandascore_cache(bucket: str, end: datetime | None) -> bool:
    if bucket != "past" or end is None:
        return False
    return end.astimezone(timezone.utc) < datetime.now(timezone.utc) - timedelta(days=2)


def parse_pandascore_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_cs2_event_id(event_id: str) -> int | None:
    match = re.fullmatch(r"cs2-(\d+)", event_id)
    if not match:
        return None
    return int(match.group(1))


def cs2_match_details(event_id: str, item: dict) -> dict:
    title = cs2_match_title(item)
    facts = cs2_match_facts(item)
    sections = [
        cs2_score_section(item),
        cs2_games_section(item),
        grid_cs2_section(event_id),
        cs2_stats_availability_section(item),
    ]
    return {
        "event_id": event_id,
        "sport": "cs2",
        "source": "pandascore",
        "summary": cs2_match_summary(item, title),
        "facts": facts,
        "sections": [section for section in sections if section is not None],
    }


def cs2_match_title(item: dict) -> str:
    opponents = [opponent.get("opponent", {}).get("name", "") for opponent in item.get("opponents", [])]
    return " vs ".join([name for name in opponents if name]) or item.get("name", "CS2 match")


def cs2_match_summary(item: dict, title: str) -> str:
    status = item.get("status", "unknown")
    competition = cs2_competition_name(item)
    score = cs2_score_summary(item, title)
    winner = (item.get("winner") or {}).get("name")
    parts = [title]
    if score:
        parts.append(score)
    if winner:
        parts.append(f"winner: {winner}")
    if competition:
        parts.append(competition)
    parts.append(f"status: {status}")
    return " | ".join(parts)


def cs2_match_facts(item: dict) -> list[dict]:
    return [
        {"label": "Match ID", "value": str(item.get("id", "-"))},
        {"label": "Format", "value": cs2_format(item)},
        {"label": "League", "value": str((item.get("league") or {}).get("name") or "-")},
        {"label": "Serie", "value": str((item.get("serie") or {}).get("full_name") or "-")},
        {"label": "Tournament", "value": str((item.get("tournament") or {}).get("name") or "-")},
    ]


def cs2_score_section(item: dict) -> dict | None:
    opponents = [opponent.get("opponent", {}) for opponent in item.get("opponents", [])]
    if not opponents:
        return None
    scores_by_team = {result.get("team_id"): result.get("score") for result in item.get("results") or []}
    rankings = hltv_rankings()
    rows = []
    for opponent in opponents:
        team_id = opponent.get("id")
        rank = find_hltv_team_rank(str(opponent.get("name") or ""), rankings)
        rows.append(
            [
                str(opponent.get("acronym") or "-"),
                str(opponent.get("name") or "-"),
                str(scores_by_team.get(team_id, "-")),
                hltv_rank_text(rank),
                hltv_points_text(rank),
                rank.tier if rank else "-",
                str(opponent.get("location") or "-"),
            ]
        )
    return {"title": "Match score", "columns": ["Tag", "Team", "Score", "HLTV", "HLTV pts", "Tier", "Country"], "rows": rows}


def cs2_games_section(item: dict) -> dict | None:
    games = item.get("games") or []
    if not games:
        return None
    team_tags = [
        str(opponent.get("opponent", {}).get("acronym") or short_team_tag(str(opponent.get("opponent", {}).get("name") or "")))
        for opponent in item.get("opponents", [])
    ]
    rows = []
    for game in sorted(games, key=lambda value: value.get("position") or 0):
        rows.append(
            [
                str(game.get("position") or "-"),
                " vs ".join([tag for tag in team_tags if tag]) or "-",
                format_seconds(game.get("length")),
            ]
        )
    return {"title": "Maps", "columns": ["Map", "Teams", "Length"], "rows": rows}


def cs2_stats_availability_section(item: dict) -> dict | None:
    if not item.get("detailed_stats"):
        return None
    return {
        "title": "Stats coverage",
        "columns": ["Provider", "Note"],
        "rows": [
            [
                "GRID",
                cs2_grid_stats_note(),
            ]
        ],
    }


def cs2_grid_stats_note() -> str:
    if os.getenv("GRID_SERIES_IDS", "").strip():
        return "GRID token and manual series bindings are configured. Open a mapped match to load cached GRID end-state stats."
    if os.getenv("GRID_API_TOKEN", "").strip():
        return (
            "GRID token is configured. Detailed map and player stats still need a GRID match binding "
            "through GRID_SERIES_IDS."
        )
    return "GRID token is not configured. Add GRID_API_TOKEN to enable the future CS2 stats provider."


def cs2_competition_name(item: dict) -> str:
    league = (item.get("league") or {}).get("name")
    serie = (item.get("serie") or {}).get("full_name")
    tournament = (item.get("tournament") or {}).get("name")
    return " - ".join([part for part in [league, serie, tournament] if part])


def cs2_format(item: dict) -> str:
    games = item.get("number_of_games")
    if games:
        return f"Bo{games}"
    return str(item.get("match_type") or "-")


def hltv_rank_text(rank: object | None) -> str:
    if rank is None:
        return "-"
    return f"#{rank.rank}"


def hltv_points_text(rank: object | None) -> str:
    if rank is None:
        return "-"
    return f"{rank.points}"


def short_team_tag(name: str) -> str:
    normalized = name.strip()
    aliases = {
        "natus vincere": "NAVI",
        "natus vincere junior": "NAVI.J",
        "team spirit": "Spirit",
        "team falcons": "Falcons",
        "g2 esports": "G2",
        "fut esports": "FUT",
        "mouz": "MOUZ",
        "furia": "FURIA",
    }
    alias = aliases.get(normalized.lower())
    if alias:
        return alias
    parts = [part for part in re.split(r"\s+", normalized) if part]
    if len(parts) <= 2 and len(normalized) <= 12:
        return normalized
    acronym = "".join(part[0] for part in parts if part[0].isalnum()).upper()
    return acronym[:6] if acronym else normalized[:8]


def format_seconds(value: object) -> str:
    try:
        if value is None or value != value:
            return "-"
        seconds = int(value)
    except (TypeError, ValueError):
        return str(value or "-")
    minutes, rest = divmod(seconds, 60)
    return f"{minutes}:{rest:02d}"


def cs2_entity_ids(title: str, competition: str | None) -> list[str]:
    # NAVI and NAVI Junior are different teams. Keep NAVI broad enough for
    # "Natus Vincere" and standalone "NAVI", but do not promote academy matches.
    text = f"{title} {competition or ''}".lower()
    entities: list[str] = []
    for entity_id, aliases in cs2_team_aliases().items():
        if entity_id == "navi_cs2":
            continue
        if any(alias.lower() in text for alias in aliases):
            entities.append(entity_id)
    if is_main_navi_match(text):
        entities.append("navi_cs2")
    if "major" in text:
        entities.append("cs2_majors")
    if "iem" in text or "intel extreme masters" in text:
        entities.append("iem")
    if "blast" in text:
        entities.append("blast")
    return list(dict.fromkeys(entities)) or ["cs2_explore"]


def cs2_team_aliases() -> dict[str, list[str]]:
    from ..storage import list_entity_records

    aliases: dict[str, list[str]] = {}
    for entity_id, record in list_entity_records().items():
        if record.entity.sport != Sport.CS2 or record.entity.kind.value != "team":
            continue
        aliases[entity_id] = [record.entity.name, *record.aliases]
    return aliases


def pandascore_bound_team_entity_ids(item: dict) -> list[str]:
    from ..storage import provider_bindings_for

    bindings = provider_bindings_for("pandascore", "team_id")
    event_team_ids = {
        str(opponent.get("opponent", {}).get("id"))
        for opponent in item.get("opponents", [])
        if opponent.get("opponent", {}).get("id") is not None
    }
    return [entity_id for entity_id, values in bindings.items() if event_team_ids & set(values)]


def is_main_navi_match(text: str) -> bool:
    without_academy = re.sub(r"\bnatus\s+vincere\s+junior\b|\bnavi\s+junior\b", " ", text)
    return "natus vincere" in without_academy or re.search(r"\bnavi\b", without_academy) is not None


def cs2_status(status: str | None, starts_at: datetime | None) -> EventStatus:
    if status == "running":
        return EventStatus.LIVE
    if status == "finished":
        return EventStatus.PAST
    if status in {"canceled", "cancelled"}:
        return EventStatus.PAST
    if starts_at is None:
        return EventStatus.TBD
    if status == "not_started" and starts_at <= datetime.now(timezone.utc):
        return EventStatus.DELAYED
    return EventStatus.UPCOMING


def cs2_score_summary(item: dict, title: str) -> str | None:
    if item.get("status") not in {"finished", "running"}:
        return None
    results = item.get("results") or []
    if len(results) < 2:
        return None
    opponents = [opponent.get("opponent", {}) for opponent in item.get("opponents", [])]
    scores_by_team = {result.get("team_id"): result.get("score") for result in results}
    ordered_scores = [scores_by_team.get(opponent.get("id")) for opponent in opponents[:2]]
    if len(ordered_scores) < 2 or any(score is None for score in ordered_scores):
        ordered_scores = [result.get("score") for result in results[:2]]
    if any(score is None for score in ordered_scores):
        return None
    return f"{ordered_scores[0]}-{ordered_scores[1]}"


def cs2_importance(entity_ids: list[str]) -> int:
    if "navi_cs2" in entity_ids:
        return 92
    if any(item in entity_ids for item in ["cs2_majors", "iem", "blast"]):
        return 76
    return 40


def pages_for_bucket(bucket: str, start: datetime | None = None, end: datetime | None = None) -> int:
    if start and end:
        if bucket == "past":
            return 20
        if bucket == "upcoming":
            return 10
        return 1
    if bucket == "past":
        return 5
    if bucket == "upcoming":
        return 3
    return 1


def dedupe_matches(matches: list[dict]) -> list[dict]:
    by_id: dict[str, dict] = {}
    for match in matches:
        match_id = str(match.get("id"))
        if match_id and match_id not in by_id:
            by_id[match_id] = match
    return list(by_id.values())


def event_in_range(event: Event, start: datetime | None, end: datetime | None) -> bool:
    if event.starts_at is None:
        return start is None and end is None
    starts_at = event.starts_at.astimezone(timezone.utc)
    if start is not None and starts_at < start.astimezone(timezone.utc):
        return False
    if end is not None and starts_at >= end.astimezone(timezone.utc):
        return False
    return True
