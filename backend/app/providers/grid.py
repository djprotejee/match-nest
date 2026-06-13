from __future__ import annotations

import json
import os
from typing import Any
from urllib.request import Request, urlopen

from ..storage import get_cached_provider_payload, upsert_provider_payload_cache


GRID_PROVIDER = "grid"
GRID_API_URL = "https://api.grid.gg/file-download/end-state/grid/series/{series_id}"


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
    return parse_grid_series_ids(os.getenv("GRID_SERIES_IDS")).get(event_id)


class GridClient:
    def __init__(self, token: str | None = None, base_url: str = GRID_API_URL) -> None:
        self.token = token or os.getenv("GRID_API_TOKEN", "").strip()
        self.base_url = base_url

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def end_state(self, series_id: str) -> tuple[Any | None, str | None, bool]:
        """Return GRID end-state payload, error text, and whether cache was used."""
        cache_key = f"end-state:grid:series:{series_id}"
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


def grid_cs2_section(event_id: str) -> dict | None:
    series_id = grid_series_id_for_event(event_id)
    if not series_id:
        return None

    payload, error, from_cache = GridClient().end_state(series_id)
    rows = [["Series ID", series_id]]
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

