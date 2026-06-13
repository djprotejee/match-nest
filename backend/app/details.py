from __future__ import annotations

from copy import deepcopy
from typing import Any


DETAILS_VERSION = 2

SECTION_FIELD_MAP = {
    "score": {"score", "match score", "classification", "race classification", "qualifying classification", "sprint classification"},
    "lineups": {"lineups"},
    "timeline_events": {"match events"},
    "team_stats": {"team statistics"},
    "player_stats": {"player statistics", "driver statistics"},
    "standings_snapshot": {"standings snapshot", "championship standings", "constructor standings"},
    "bracket_snapshot": {"bracket snapshot", "bracket", "tournament bracket"},
}


def normalize_event_details(details: dict | None, raw_payload_cache_keys: list[str] | None = None) -> dict | None:
    """Return the EventDetails v2 envelope while preserving the legacy UI shape.

    The current frontend renders `facts` and `sections`. New provider work can
    rely on the typed v2 fields without forcing every UI component to change at
    once.
    """
    if details is None:
        return None
    normalized = deepcopy(details)
    normalized.setdefault("version", DETAILS_VERSION)
    normalized.setdefault("summary", "")
    normalized.setdefault("facts", [])
    normalized.setdefault("sections", [])
    normalized.setdefault("score", None)
    normalized.setdefault("lineups", [])
    normalized.setdefault("timeline_events", [])
    normalized.setdefault("team_stats", [])
    normalized.setdefault("player_stats", [])
    normalized.setdefault("standings_snapshot", [])
    normalized.setdefault("bracket_snapshot", [])
    normalized.setdefault("raw_provider_payload", None)
    normalized.setdefault("raw_payload_cache_keys", [])
    if raw_payload_cache_keys:
        normalized["raw_payload_cache_keys"] = sorted(set(normalized["raw_payload_cache_keys"]) | set(raw_payload_cache_keys))

    for section in normalized.get("sections") or []:
        field_name = details_field_for_section(section)
        if not field_name:
            continue
        rows = section_rows(section)
        if field_name == "score" and normalized.get("score") is None:
            normalized["score"] = section_to_table(section)
        elif field_name != "score" and not normalized.get(field_name):
            normalized[field_name] = rows
    return normalized


def details_field_for_section(section: dict) -> str | None:
    title = str(section.get("title") or "").strip().lower()
    if not title:
        return None
    for field_name, titles in SECTION_FIELD_MAP.items():
        if title in titles or any(title.startswith(candidate) for candidate in titles):
            return field_name
    return None


def section_rows(section: dict) -> list[dict[str, str]]:
    columns = [str(column) for column in section.get("columns") or []]
    rows = []
    for row in section.get("rows") or []:
        values = [str(value) for value in row]
        rows.append({columns[index] if index < len(columns) else f"value_{index + 1}": value for index, value in enumerate(values)})
    return rows


def section_to_table(section: dict) -> dict[str, Any]:
    return {
        "title": str(section.get("title") or ""),
        "columns": [str(column) for column in section.get("columns") or []],
        "rows": [[str(value) for value in row] for row in section.get("rows") or []],
    }
