from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .base import EventProvider
from ..models import Event, EventStatus, Sport


class PandaScoreCS2Provider(EventProvider):
    def __init__(
        self,
        token: str | None = None,
        base_url: str = "https://api.pandascore.co/csgo/matches",
    ) -> None:
        self.token = token or os.getenv("PANDASCORE_TOKEN")
        self.base_url = base_url

    def fetch(self) -> list[Event]:
        # PandaScore marks upcoming, past, and running CS match endpoints as
        # available to all plans. Fetching them separately also avoids pulling
        # unnecessary match details that may belong to paid tiers.
        if not self.token:
            return []
        payload: list[dict] = []
        for bucket in ["running", "upcoming", "past"]:
            payload.extend(self._fetch_bucket(bucket))
        return [self._match_to_event(item) for item in payload]

    def _fetch_bucket(self, bucket: str) -> list[dict]:
        query = urlencode({"sort": "begin_at", "per_page": "100"})
        request = Request(
            f"{self.base_url}/{bucket}?{query}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
        )
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))

    def _match_to_event(self, item: dict) -> Event:
        opponents = [opponent.get("opponent", {}).get("name", "") for opponent in item.get("opponents", [])]
        title = " vs ".join([name for name in opponents if name]) or item.get("name", "CS2 match")
        league = item.get("league", {}).get("name")
        serie = item.get("serie", {}).get("full_name")
        competition = " - ".join([part for part in [league, serie] if part]) or league or serie
        starts_at = parse_pandascore_datetime(item.get("begin_at"))
        entity_ids = cs2_entity_ids(title, competition)

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


def parse_pandascore_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def cs2_entity_ids(title: str, competition: str | None) -> list[str]:
    # Keep this broad for v1: NAVI can appear as "Natus Vincere" or "NAVI",
    # while big tournaments are commonly discovered by league/series names.
    text = f"{title} {competition or ''}".lower()
    entities: list[str] = []
    if "natus vincere" in text or "navi" in text:
        entities.append("navi_cs2")
    if "major" in text:
        entities.append("cs2_majors")
    if "iem" in text or "intel extreme masters" in text:
        entities.append("iem")
    if "blast" in text:
        entities.append("blast")
    return entities or ["cs2_explore"]


def cs2_status(status: str | None, starts_at: datetime | None) -> EventStatus:
    if status == "running":
        return EventStatus.LIVE
    if status == "finished":
        return EventStatus.PAST
    if starts_at is None:
        return EventStatus.TBD
    if starts_at <= datetime.now(timezone.utc):
        return EventStatus.PAST
    return EventStatus.UPCOMING


def cs2_score_summary(item: dict, title: str) -> str | None:
    # The API score array does not guarantee opponent names in this mapper, so
    # the title remains the human-readable anchor and the score is compact.
    if item.get("status") != "finished":
        return None
    results = item.get("results") or []
    if len(results) < 2:
        return None
    return f"{title} - {results[0].get('score')}-{results[1].get('score')}"


def cs2_importance(entity_ids: list[str]) -> int:
    if "navi_cs2" in entity_ids:
        return 92
    if any(item in entity_ids for item in ["cs2_majors", "iem", "blast"]):
        return 76
    return 40
