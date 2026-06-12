from __future__ import annotations

import json
import os
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen

from .models import EntityBinding, EntityKind, Sport, UserPreferences
from .storage import list_entity_records


def entity_payload(record, preferences: UserPreferences, include_detail: bool = False) -> dict:
    entity = record.entity
    payload = {
        "id": entity.id,
        "name": entity.name,
        "sport": entity.sport.value,
        "kind": entity.kind.value,
        "color": entity.color,
        "follow": follow_level_value(preferences.follows.get(entity.id).level) if entity.id in preferences.follows else "explore",
    }
    if include_detail:
        payload["aliases"] = record.aliases
        payload["bindings"] = [
            {
                "provider": binding.provider,
                "binding_type": binding.binding_type,
                "value": binding.value,
                "metadata": binding.metadata,
            }
            for binding in record.bindings
        ]
        payload["is_seed"] = record.is_seed
    return payload


def default_entity_color(sport: Sport, kind: EntityKind) -> str:
    if sport == Sport.FORMULA:
        return "#F04438"
    if sport == Sport.CS2:
        return "#F4B740"
    if kind == EntityKind.COMPETITION:
        return "#8B5CF6"
    return "#2ECC71"


def follow_level_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def entity_bindings_from_payload(bindings) -> list[EntityBinding]:
    return [
        EntityBinding(item.provider.strip(), item.binding_type.strip(), item.value.strip())
        for item in bindings
        if item.provider.strip() and item.binding_type.strip() and item.value.strip()
    ]


def provider_search_candidates(query: str) -> list[dict]:
    search_text = query.lower().strip()
    candidates: list[dict] = []
    for record in list_entity_records().values():
        text = f"{record.entity.name} {' '.join(record.aliases)}".lower()
        if search_text not in text:
            continue
        candidates.append(
            {
                "provider": "matchnest",
                "name": record.entity.name,
                "sport": record.entity.sport.value,
                "kind": record.entity.kind.value,
                "aliases": record.aliases,
                "bindings": [
                    {"provider": binding.provider, "binding_type": binding.binding_type, "value": binding.value}
                    for binding in record.bindings
                ],
            }
        )
    candidates.extend(api_football_candidates(query))
    candidates.extend(pandascore_team_candidates(query))
    return candidates[:20]


def api_football_candidates(query: str) -> list[dict]:
    token = os.getenv("API_FOOTBALL_TOKEN", "").strip()
    if not token:
        return []
    try:
        request = UrlRequest(
            f"https://v3.football.api-sports.io/teams?{urlencode({'search': query})}",
            headers={"x-apisports-key": token, "Accept": "application/json"},
        )
        with urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return []
    output = []
    for item in payload.get("response", [])[:8]:
        team = item.get("team") or {}
        team_id = team.get("id")
        name = team.get("name")
        if not team_id or not name:
            continue
        output.append(
            {
                "provider": "api-football",
                "name": name,
                "sport": "football",
                "kind": "team",
                "aliases": [name, str(team.get("code") or "")],
                "bindings": [{"provider": "api-football", "binding_type": "team_id", "value": str(team_id)}],
            }
        )
    return output


def pandascore_team_candidates(query: str) -> list[dict]:
    token = os.getenv("PANDASCORE_TOKEN", "").strip()
    if not token:
        return []
    try:
        request = UrlRequest(
            f"https://api.pandascore.co/csgo/teams?{urlencode({'search[name]': query, 'per_page': '8'})}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        with urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return []
    output = []
    for team in payload[:8]:
        team_id = team.get("id")
        name = team.get("name")
        if not team_id or not name:
            continue
        output.append(
            {
                "provider": "pandascore",
                "name": name,
                "sport": "cs2",
                "kind": "team",
                "aliases": [name, str(team.get("acronym") or "")],
                "bindings": [{"provider": "pandascore", "binding_type": "team_id", "value": str(team_id)}],
            }
        )
    return output
