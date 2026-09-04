from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

from .base import EventProvider
from ..models import Event, EventStatus, F1Session, Sport
from ..storage import get_cached_provider_payload, upsert_provider_payload_cache


PRACTICE_SESSION_FILTERS = {
    "firstpractice": "FP1",
    "secondpractice": "FP2",
    "thirdpractice": "FP3",
}
PRACTICE_SESSION_TITLES = {
    "firstpractice": "Practice 1",
    "secondpractice": "Practice 2",
    "thirdpractice": "Practice 3",
}
ALPHA_TO_ERGAST_SESSION_KEYS = {
    "SQ": "SprintQualifying",
    "FP1": "FirstPractice",
    "FP2": "SecondPractice",
    "FP3": "ThirdPractice",
}
ERGAST_SESSION_KEYS = {
    "qualifying": "Qualifying",
    "sprint": "Sprint",
}
FASTF1_SESSION_FILTERS = {"SQ", "FP1", "FP2", "FP3"}
FASTF1_CACHE_DIR = Path(__file__).resolve().parents[2] / ".data" / "fastf1-cache"


class JolpicaF1Provider(EventProvider):
    def __init__(self, base_url: str = "https://api.jolpi.ca/ergast/f1", season: str = "current") -> None:
        self.base_url = base_url.rstrip("/")
        self.alpha_base_url = "https://api.jolpi.ca/f1/alpha"
        self.season = season
        self.url = f"{self.base_url}/{self.season}.json"

    def fetch(self, start: datetime | None = None, end: datetime | None = None) -> list[Event]:
        payload = fetch_json(self.url)

        races = payload.get("MRData", {}).get("RaceTable", {}).get("Races", [])
        events: list[Event] = []
        for race in races:
            race_name = race.get("raceName", "Formula 1 Grand Prix")
            round_id = race.get("round", "0")
            events.extend(self._race_sessions(race, race_name, round_id))
        return [event for event in events if event.starts_at and (start is None or event.starts_at >= start) and (end is None or event.starts_at < end)]

    def _race_sessions(self, race: dict, race_name: str, round_id: str) -> list[Event]:
        # Jolpica exposes a race weekend as one race object with nested session
        # timestamps. MatchNest stores each session as a separate event so the
        # user can hide practice but keep qualifying, sprint, and race alerts.
        sessions: list[tuple[str, str, F1Session, int]] = [
            ("race", race_name, F1Session.RACE, 95),
            ("Qualifying", f"{race_name} - Qualifying", F1Session.QUALIFYING, 82),
            ("Sprint", f"{race_name} - Sprint", F1Session.SPRINT, 78),
            ("SprintQualifying", f"{race_name} - Sprint Qualifying", F1Session.QUALIFYING, 70),
            ("FirstPractice", f"{race_name} - Practice 1", F1Session.PRACTICE, 35),
            ("SecondPractice", f"{race_name} - Practice 2", F1Session.PRACTICE, 30),
            ("ThirdPractice", f"{race_name} - Practice 3", F1Session.PRACTICE, 30),
        ]

        output: list[Event] = []
        for key, title, session_type, importance in sessions:
            source = race if key == "race" else race.get(key)
            starts_at = parse_utc_datetime(source)
            if starts_at is None:
                continue
            output.append(
                Event(
                    id=f"f1-{race.get('season', 'current')}-{round_id}-{key.lower()}",
                    title=title,
                    sport=Sport.FORMULA,
                    starts_at=starts_at,
                    status=status_for(starts_at),
                    entity_ids=["f1", "ferrari"],
                    source="jolpica",
                    competition="Formula 1",
                    session_type=session_type,
                    importance=importance,
                )
            )
        return output

    def details(self, event_id: str) -> dict | None:
        parsed = parse_f1_event_id(event_id)
        if parsed is None:
            return None
        season, round_id, session_key = parsed

        if session_key == "race":
            return self._race_details(season, round_id)
        if session_key == "qualifying":
            return self._qualifying_details(season, round_id)
        if session_key == "sprint":
            return self._sprint_details(season, round_id)
        if session_key == "sprintqualifying":
            return self._alpha_session_details(season, round_id, "SQ", event_id, "Sprint Qualifying classification")
        if session_key in PRACTICE_SESSION_FILTERS:
            return self._alpha_session_details(
                season,
                round_id,
                PRACTICE_SESSION_FILTERS[session_key],
                event_id,
                f"{PRACTICE_SESSION_TITLES[session_key]} classification",
            )

        return {
            "event_id": event_id,
            "sport": "formula",
            "source": "jolpica",
            "summary": "Classification is not available from the current Jolpica Ergast-compatible endpoint for this session type.",
            "facts": [],
            "sections": [],
        }

    def _race_details(self, season: str, round_id: str) -> dict | None:
        race = first_race(fetch_json(f"{self.base_url}/{season}/{round_id}/results.json"))
        if race is None:
            return self._scheduled_session_details(season, round_id, "race", f"f1-{season}-{round_id}-race", "Race classification")
        results = race.get("Results", [])
        fastest = fastest_lap_result(results)
        strategies = fastf1_strategy_by_driver(season, round_id, "R")
        sections = [
            {
                "title": "Race classification",
                "columns": [
                    "Pos",
                    "DRV",
                    "No",
                    "Driver",
                    "Team",
                    "Grid",
                    "Laps",
                    "Time / status",
                    "Points",
                    "Fastest lap",
                    "Tyre stints",
                    "Pit / changes",
                ],
                "rows": [race_result_row(item, strategies) for item in results],
            }
        ]
        sections.extend(f1_standings_sections(self.base_url, season, round_id))

        return {
            "event_id": f"f1-{season}-{round_id}-race",
            "sport": "formula",
            "source": "jolpica",
            "summary": race_summary(race),
            "facts": race_facts(race, fastest),
            "sections": sections,
            "raw_payload_cache_keys": f1_raw_cache_keys(
                self.base_url,
                [
                    f"{season}/{round_id}/results.json",
                    f"{season}/{round_id}/driverStandings.json",
                    f"{season}/{round_id}/constructorStandings.json",
                ],
            ),
        }

    def _qualifying_details(self, season: str, round_id: str) -> dict | None:
        race = first_race(fetch_json(f"{self.base_url}/{season}/{round_id}/qualifying.json"))
        if race is None:
            return self._scheduled_session_details(
                season,
                round_id,
                "qualifying",
                f"f1-{season}-{round_id}-qualifying",
                "Qualifying classification",
            )
        results = race.get("QualifyingResults", [])
        sections = [
            {
                "title": "Qualifying classification",
                "columns": ["Pos", "DRV", "No", "Driver", "Team", "Q1", "Q2", "Q3"],
                "rows": [qualifying_result_row(item) for item in results],
            }
        ]
        sections.extend(f1_standings_sections(self.base_url, season, round_id))

        return {
            "event_id": f"f1-{season}-{round_id}-qualifying",
            "sport": "formula",
            "source": "jolpica",
            "summary": race_summary(race),
            "facts": race_facts(race, None),
            "sections": sections,
            "raw_payload_cache_keys": f1_raw_cache_keys(
                self.base_url,
                [
                    f"{season}/{round_id}/qualifying.json",
                    f"{season}/{round_id}/driverStandings.json",
                    f"{season}/{round_id}/constructorStandings.json",
                ],
            ),
        }

    def _sprint_details(self, season: str, round_id: str) -> dict | None:
        race = first_race(fetch_json(f"{self.base_url}/{season}/{round_id}/sprint.json"))
        if race is None:
            return self._scheduled_session_details(season, round_id, "sprint", f"f1-{season}-{round_id}-sprint", "Sprint classification")
        results = race.get("SprintResults", [])
        fastest = fastest_lap_result(results)
        strategies = fastf1_strategy_by_driver(season, round_id, "S")
        sections = [
            {
                "title": "Sprint classification",
                "columns": [
                    "Pos",
                    "DRV",
                    "No",
                    "Driver",
                    "Team",
                    "Grid",
                    "Laps",
                    "Time / status",
                    "Points",
                    "Fastest lap",
                    "Tyre stints",
                    "Pit / changes",
                ],
                "rows": [race_result_row(item, strategies) for item in results],
            }
        ]
        sections.extend(f1_standings_sections(self.base_url, season, round_id))

        return {
            "event_id": f"f1-{season}-{round_id}-sprint",
            "sport": "formula",
            "source": "jolpica",
            "summary": race_summary(race),
            "facts": race_facts(race, fastest),
            "sections": sections,
            "raw_payload_cache_keys": f1_raw_cache_keys(
                self.base_url,
                [
                    f"{season}/{round_id}/sprint.json",
                    f"{season}/{round_id}/driverStandings.json",
                    f"{season}/{round_id}/constructorStandings.json",
                ],
            ),
        }

    def _alpha_session_details(
        self,
        season: str,
        round_id: str,
        session_filter: str,
        event_id: str,
        section_title: str,
    ) -> dict | None:
        alpha_round_id = self._alpha_round_id(season, round_id)
        if alpha_round_id is None:
            return self._session_facts_details(season, round_id, session_filter, event_id, section_title)

        payload = fetch_json(f"{self.alpha_base_url}/results/{alpha_round_id}/{session_filter}/")
        data = payload.get("data", {})
        results = data.get("results", [])

        sections = []
        if results:
            columns, rows = alpha_result_table(results, data.get("component_keys", []))
            sections.append({"title": section_title, "columns": columns, "rows": rows})

        if not sections and session_filter in FASTF1_SESSION_FILTERS:
            fastf1_details = fastf1_session_details(season, round_id, session_filter, event_id, data)
            if fastf1_details is not None:
                return fastf1_details

        summary = alpha_session_summary(data)
        if not results:
            summary = f"{summary}. Classification is not published by Jolpica for this session yet."

        return {
            "event_id": event_id,
            "sport": "formula",
            "source": "jolpica",
            "summary": summary,
            "facts": alpha_session_facts(data),
            "sections": sections,
            "raw_payload_cache_keys": [
                f"jolpica:{json_cache_key(f'{self.alpha_base_url}/schedules/{season}/')}",
                f"jolpica:{json_cache_key(f'{self.alpha_base_url}/results/{alpha_round_id}/{session_filter}/')}",
            ],
        }

    def _session_facts_details(
        self,
        season: str,
        round_id: str,
        session_filter: str,
        event_id: str,
        section_title: str,
    ) -> dict | None:
        race = first_race(fetch_json(f"{self.base_url}/{season}/{round_id}.json"))
        if race is None:
            return None
        key = ALPHA_TO_ERGAST_SESSION_KEYS.get(session_filter)
        session = race.get(key, {}) if key else {}
        facts = race_facts(race, None)
        starts_at = parse_utc_datetime(session)
        if starts_at:
            facts.append({"label": "Start", "value": starts_at.isoformat()})
        return {
            "event_id": event_id,
            "sport": "formula",
            "source": "jolpica",
            "summary": f"{section_title.replace(' classification', '')} at {race_summary(race)}. Classification is not published by Jolpica for this session yet.",
            "facts": facts,
            "sections": [],
            "raw_payload_cache_keys": f1_raw_cache_keys(self.base_url, [f"{season}/{round_id}.json"]),
        }

    def _scheduled_session_details(
        self,
        season: str,
        round_id: str,
        session_key: str,
        event_id: str,
        section_title: str,
    ) -> dict | None:
        race = first_race(fetch_json(f"{self.base_url}/{season}/{round_id}.json"))
        if race is None:
            return None
        session = race if session_key == "race" else race.get(ERGAST_SESSION_KEYS.get(session_key, ""), {})
        facts = race_facts(race, None)
        starts_at = parse_utc_datetime(session)
        if starts_at:
            facts.append({"label": "Start", "value": starts_at.isoformat()})
        return {
            "event_id": event_id,
            "sport": "formula",
            "source": "jolpica",
            "summary": f"{section_title.replace(' classification', '')} at {race_summary(race)}. Classification is not published by Jolpica for this session yet.",
            "facts": facts,
            "sections": [],
            "raw_payload_cache_keys": f1_raw_cache_keys(self.base_url, [f"{season}/{round_id}.json"]),
        }

    def _alpha_round_id(self, season: str, round_id: str) -> str | None:
        if season == "current":
            season = str(datetime.now(timezone.utc).year)
        payload = fetch_json(f"{self.alpha_base_url}/schedules/{season}/")
        events = payload.get("data", {}).get("events", [])
        for event in events:
            round_info = event.get("round", {})
            if str(round_info.get("number")) == str(round_id):
                return round_info.get("id")
        return None


def parse_utc_datetime(item: dict | None) -> datetime | None:
    # Ergast-compatible payloads split date and time. Time is UTC when present.
    if not item or "date" not in item:
        return None
    time_value = item.get("time", "00:00:00Z")
    value = f"{item['date']}T{time_value}".replace("Z", "+00:00")
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def status_for(starts_at: datetime) -> EventStatus:
    now = datetime.now(timezone.utc)
    if starts_at <= now:
        return EventStatus.PAST
    return EventStatus.UPCOMING


def fetch_json(url: str) -> dict:
    cache_key = json_cache_key(url)
    try:
        with urlopen(url, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        cached = get_cached_provider_payload("jolpica", cache_key)
        return cached if isinstance(cached, dict) else {}
    upsert_provider_payload_cache("jolpica", cache_key, payload)
    return payload


def json_cache_key(url: str) -> str:
    return f"json:{url}"


def f1_raw_cache_keys(base_url: str, paths: list[str]) -> list[str]:
    root = base_url.rstrip("/")
    return [f"jolpica:{json_cache_key(f'{root}/{path}')}" for path in paths]


def first_race(payload: dict) -> dict | None:
    races = payload.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    return races[0] if races else None


def f1_standings_sections(base_url: str, season: str, round_id: str) -> list[dict]:
    sections = []
    driver_section = f1_driver_standings_section(fetch_json(f"{base_url}/{season}/{round_id}/driverStandings.json"))
    constructor_section = f1_constructor_standings_section(fetch_json(f"{base_url}/{season}/{round_id}/constructorStandings.json"))
    if driver_section:
        sections.append(driver_section)
    if constructor_section:
        sections.append(constructor_section)
    return sections


def f1_driver_standings_section(payload: dict) -> dict | None:
    standings = first_standings_list(payload).get("DriverStandings", [])
    if not standings:
        return None
    rows = []
    for item in standings:
        driver = item.get("Driver") or {}
        constructors = item.get("Constructors") or []
        constructor = constructors[0] if constructors else {}
        rows.append(
            [
                str(item.get("position") or "-"),
                str(driver.get("code") or "-"),
                str(driver.get("permanentNumber") or "-"),
                f"{driver.get('givenName', '')} {driver.get('familyName', '')}".strip() or "-",
                str(constructor.get("name") or "-"),
                str(item.get("points") or "-"),
                str(item.get("wins") or "0"),
            ]
        )
    return {
        "title": "Championship standings",
        "columns": ["Pos", "DRV", "No", "Driver", "Team", "Points", "Wins"],
        "rows": rows,
    }


def f1_constructor_standings_section(payload: dict) -> dict | None:
    standings = first_standings_list(payload).get("ConstructorStandings", [])
    if not standings:
        return None
    rows = []
    for item in standings:
        constructor = item.get("Constructor") or {}
        rows.append(
            [
                str(item.get("position") or "-"),
                str(constructor.get("name") or "-"),
                str(constructor.get("nationality") or "-"),
                str(item.get("points") or "-"),
                str(item.get("wins") or "0"),
            ]
        )
    return {
        "title": "Constructor standings",
        "columns": ["Pos", "Constructor", "Nationality", "Points", "Wins"],
        "rows": rows,
    }


def first_standings_list(payload: dict) -> dict:
    lists = payload.get("MRData", {}).get("StandingsTable", {}).get("StandingsLists", [])
    return lists[0] if lists else {}


def parse_f1_event_id(event_id: str) -> tuple[str, str, str] | None:
    match = re.fullmatch(r"f1-(?P<season>\d{4}|current)-(?P<round>\d+)-(?P<session>[a-z]+)", event_id)
    if not match:
        return None
    return match.group("season"), match.group("round"), match.group("session")


def driver_name(item: dict) -> str:
    driver = item.get("Driver", {})
    code = driver.get("code")
    name = driver_full_name(item)
    return f"{code} - {name}" if code and name else name or code or "Unknown driver"


def driver_code(item: dict) -> str:
    return item.get("Driver", {}).get("code", "")


def driver_number(item: dict) -> str:
    return str(item.get("number") or item.get("Driver", {}).get("permanentNumber") or "-")


def driver_full_name(item: dict) -> str:
    driver = item.get("Driver", {})
    return f"{driver.get('givenName', '')} {driver.get('familyName', '')}".strip() or "Unknown driver"


def constructor_name(item: dict) -> str:
    return item.get("Constructor", {}).get("name", "Unknown team")


def race_summary(race: dict) -> str:
    circuit = race.get("Circuit", {})
    location = circuit.get("Location", {})
    place = ", ".join(part for part in [location.get("locality"), location.get("country")] if part)
    circuit_name = circuit.get("circuitName")
    if circuit_name and place:
        return f"{race.get('raceName', 'Formula 1 session')} at {circuit_name}, {place}"
    return race.get("raceName", "Formula 1 session")


def race_facts(race: dict, fastest: dict | None) -> list[dict]:
    facts = [
        {"label": "Season", "value": race.get("season", "-")},
        {"label": "Round", "value": race.get("round", "-")},
        {"label": "Circuit", "value": race.get("Circuit", {}).get("circuitName", "-")},
    ]
    if fastest:
        fastest_lap = fastest.get("FastestLap", {})
        facts.append(
            {
                "label": "Fastest lap",
                "value": f"{driver_name(fastest)} - lap {fastest_lap.get('lap', '-')}, {fastest_lap.get('Time', {}).get('time', '-')}",
            }
        )
    return facts


def fastest_lap_result(results: list[dict]) -> dict | None:
    for item in results:
        if item.get("FastestLap", {}).get("rank") == "1":
            return item
    return None


def race_result_row(item: dict, strategies: dict[str, dict[str, str]] | None = None) -> list[str]:
    fastest_lap = item.get("FastestLap", {})
    fastest_text = ""
    if fastest_lap:
        fastest_text = f"Lap {fastest_lap.get('lap', '-')}, {fastest_lap.get('Time', {}).get('time', '-')}"

    row = [
        item.get("positionText") or item.get("position", "-"),
        driver_code(item),
        driver_number(item),
        driver_full_name(item),
        constructor_name(item),
        item.get("grid", "-"),
        item.get("laps", "-"),
        item.get("Time", {}).get("time") or item.get("status", "-"),
        item.get("points", "0"),
        fastest_text,
    ]
    if strategies is not None:
        strategy = strategies.get(driver_code(item), {})
        row.extend([strategy.get("stints", "-"), strategy.get("changes", "-")])
    return row


def qualifying_result_row(item: dict) -> list[str]:
    return [
        item.get("position", "-"),
        driver_code(item),
        driver_number(item),
        driver_full_name(item),
        constructor_name(item),
        item.get("Q1", "-"),
        item.get("Q2", "-"),
        item.get("Q3", "-"),
    ]


def alpha_session_summary(data: dict) -> str:
    round_info = data.get("round", {})
    circuit = data.get("circuit", {})
    title = data.get("title", "Formula 1 session")
    round_name = round_info.get("name", "Formula 1 Grand Prix")
    circuit_name = circuit.get("name")
    locality = circuit.get("locality")
    country = circuit.get("country") or circuit.get("country_code")
    place = ", ".join(part for part in [locality, country] if part)
    if circuit_name and place:
        return f"{title} for {round_name} at {circuit_name}, {place}"
    if circuit_name:
        return f"{title} for {round_name} at {circuit_name}"
    return f"{title} for {round_name}"


def alpha_session_facts(data: dict) -> list[dict]:
    round_info = data.get("round", {})
    circuit = data.get("circuit", {})
    season = data.get("season", {})
    facts = [
        {"label": "Session", "value": data.get("title", "-")},
        {"label": "Season", "value": str(season.get("year", "-"))},
        {"label": "Round", "value": str(round_info.get("number", "-"))},
        {"label": "Circuit", "value": circuit.get("name", "-")},
    ]
    if data.get("timestamp"):
        facts.append({"label": "Start", "value": data["timestamp"]})
    if data.get("local_timestamp"):
        facts.append({"label": "Local start", "value": data["local_timestamp"]})
    if data.get("timezone"):
        facts.append({"label": "Track timezone", "value": data["timezone"]})
    return facts


def alpha_result_table(results: list[dict], component_keys: list[str]) -> tuple[list[str], list[list[str]]]:
    columns = ["Pos", "DRV", "No", "Driver", "Team"]
    if component_keys:
        columns.extend(component_keys)
    else:
        columns.append("Time")

    race_like_components = {"GRID", "FLAP"}
    if race_like_components.intersection(component_keys):
        columns = ["Pos", "DRV", "No", "Driver", "Team", "Grid", "Laps", "Time / status", "Points", "Fastest lap"]
        return columns, [alpha_race_like_row(item) for item in results]

    return columns, [alpha_timed_session_row(item, component_keys) for item in results]


def alpha_timed_session_row(item: dict, component_keys: list[str]) -> list[str]:
    row = [
        str(item.get("position_text") or item.get("position") or "-"),
        alpha_driver_code(item),
        alpha_driver_number(item),
        alpha_driver_full_name(item),
        alpha_team_name(item),
    ]
    if not component_keys:
        row.append(str(item.get("time") or item.get("status") or "-"))
        return row

    components = item.get("components", {})
    for key in component_keys:
        component = components.get(key, {})
        row.append(str(component.get("time") or component.get("position") or "-"))
    return row


def alpha_race_like_row(item: dict) -> list[str]:
    components = item.get("components", {})
    grid = components.get("GRID", {})
    fastest_lap = components.get("FLAP", {})
    fastest_text = ""
    if fastest_lap:
        fastest_text = str(fastest_lap.get("time") or fastest_lap.get("position") or "")
    return [
        str(item.get("position_text") or item.get("position") or "-"),
        alpha_driver_code(item),
        alpha_driver_number(item),
        alpha_driver_full_name(item),
        alpha_team_name(item),
        str(grid.get("position", "-")),
        str(item.get("laps", "-")),
        str(item.get("time") or item.get("status") or "-"),
        str(item.get("points", "0")),
        fastest_text,
    ]


def alpha_driver_name(item: dict) -> str:
    driver = item.get("driver", {})
    code = alpha_driver_code(item)
    name = alpha_driver_full_name(item)
    return f"{code} - {name}" if code and name else name or code or "Unknown driver"


def alpha_driver_code(item: dict) -> str:
    return item.get("driver", {}).get("abbreviation", "")


def alpha_driver_number(item: dict) -> str:
    return str(item.get("car_number") or "-")


def alpha_driver_full_name(item: dict) -> str:
    driver = item.get("driver", {})
    return f"{driver.get('given_name', '')} {driver.get('family_name', '')}".strip() or "Unknown driver"


def alpha_team_name(item: dict) -> str:
    return item.get("team", {}).get("name", "Unknown team")


def fastf1_session_details(
    season: str,
    round_id: str,
    session_filter: str,
    event_id: str,
    alpha_data: dict | None = None,
) -> dict | None:
    try:
        import fastf1
    except ImportError:
        return None

    try:
        FASTF1_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        fastf1.Cache.enable_cache(str(FASTF1_CACHE_DIR))
        if hasattr(fastf1, "set_log_level"):
            fastf1.set_log_level("WARNING")
        session = fastf1.get_session(int(season), int(round_id), session_filter)
        session.load(laps=True, telemetry=False, weather=False, messages=session_filter == "SQ")
    except Exception:
        return None

    try:
        if session_filter == "SQ":
            section = fastf1_qualifying_section(session, "Sprint Qualifying classification")
        else:
            section = fastf1_practice_section(session, f"{session.name} best laps")
    except Exception:
        return None
    if section is None:
        return None

    return {
        "event_id": event_id,
        "sport": "formula",
        "source": "fastf1",
        "summary": fastf1_summary(session, alpha_data),
        "facts": fastf1_facts(session, alpha_data),
        "sections": [section],
    }


def fastf1_strategy_by_driver(season: str, round_id: str, session_filter: str) -> dict[str, dict[str, str]]:
    try:
        import fastf1
    except ImportError:
        return {}

    try:
        FASTF1_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        fastf1.Cache.enable_cache(str(FASTF1_CACHE_DIR))
        if hasattr(fastf1, "set_log_level"):
            fastf1.set_log_level("WARNING")
        session = fastf1.get_session(int(season), int(round_id), session_filter)
        session.load(laps=True, telemetry=False, weather=False, messages=False)
    except Exception:
        return {}

    laps = getattr(session, "laps", None)
    if laps is None or laps.empty:
        return {}

    strategies: dict[str, dict[str, str]] = {}
    for driver, driver_laps in laps.groupby("Driver"):
        sorted_laps = driver_laps.sort_values("LapNumber")
        stints = detailed_tyre_stints(sorted_laps, split_on_pit_out=session_filter == "R")
        changes = pit_change_events(sorted_laps)
        if stints or changes:
            strategies[str(driver)] = {
                "stints": " • ".join(stints) if stints else "-",
                "changes": " • ".join(changes) if changes else "-",
            }
    return strategies


def detailed_tyre_stints(driver_laps: object, split_on_pit_out: bool = True) -> list[str]:
    stints: list[str] = []
    last_compound = None
    last_tyre_life: float | None = None
    for _, lap in driver_laps.iterrows():
        compound = str(lap.get("Compound") or "-")
        lap_number = format_number(lap.get("LapNumber"))
        if lap_number == "-" or compound == "-":
            continue
        pit_out = lap.get("PitOutTime")
        has_pit_out = pit_out == pit_out
        tyre_life_value = numeric_value(lap.get("TyreLife"))
        tyre_life_reset = last_tyre_life is not None and tyre_life_value is not None and tyre_life_value <= last_tyre_life
        starts_new_stint = (
            last_compound is None
            or compound != last_compound
            or (split_on_pit_out and has_pit_out and lap_number != "1" and tyre_life_reset)
        )
        last_tyre_life = tyre_life_value if tyre_life_value is not None else last_tyre_life
        if not starts_new_stint:
            continue
        short_compound = short_tyre_compound(compound)
        tyre_life = format_number(lap.get("TyreLife"))
        fresh = lap.get("FreshTyre")
        freshness = "new" if fresh is True else f"{tyre_life} old" if tyre_life != "-" else "used"
        stints.append(f"L{lap_number} {short_compound} ({freshness})")
        last_compound = compound
    return stints


def pit_change_events(driver_laps: object) -> list[str]:
    events: list[str] = []
    rows = list(driver_laps.iterrows())
    for index, (_, lap) in enumerate(rows):
        pit_in = lap.get("PitInTime")
        pit_out = lap.get("PitOutTime")
        has_pit_in = pit_in == pit_in
        has_pit_out = pit_out == pit_out
        if not has_pit_in and not has_pit_out:
            continue
        if has_pit_out and not has_pit_in and has_recent_pit_in(rows, index):
            continue
        lap_number = format_number(lap.get("LapNumber"))
        compound = short_tyre_compound(str(lap.get("Compound") or "-"))
        context = track_status_label(lap.get("TrackStatus"))
        duration = "-"
        if has_pit_in:
            out_time = pit_out if has_pit_out else next_pit_out_time(rows, index)
            duration = "red flag stop" if "red flag" in context else format_pit_duration(pit_in, out_time)
        action = "pit" if has_pit_in else "out/restart"
        events.append(f"L{lap_number} {action} {compound} [{context}, {duration}]")
    return events


def has_recent_pit_in(rows: list[tuple[object, object]], index: int) -> bool:
    for _, lap in rows[max(0, index - 2) : index]:
        pit_in = lap.get("PitInTime")
        if pit_in == pit_in:
            return True
    return False


def next_pit_out_time(rows: list[tuple[object, object]], start_index: int) -> object | None:
    for _, lap in rows[start_index + 1 : start_index + 3]:
        pit_out = lap.get("PitOutTime")
        if pit_out == pit_out:
            return pit_out
    return None


def format_pit_duration(pit_in: object, pit_out: object | None) -> str:
    if pit_out is None:
        return "-"
    try:
        seconds = (pit_out - pit_in).total_seconds()
    except Exception:
        return "-"
    if seconds < 0:
        return "-"
    return f"{seconds:.1f}s"


def track_status_label(value: object) -> str:
    status = str(value or "")
    labels = []
    if "5" in status:
        labels.append("red flag")
    if "4" in status:
        labels.append("SC")
    if "6" in status or "7" in status:
        labels.append("VSC")
    if "2" in status:
        labels.append("yellow")
    if not labels and "1" in status:
        labels.append("green")
    return "+".join(labels) if labels else "-"


def short_tyre_compound(compound: str) -> str:
    normalized = compound.upper()
    if normalized.startswith("SOFT"):
        return "S"
    if normalized.startswith("MEDIUM"):
        return "M"
    if normalized.startswith("HARD"):
        return "H"
    if normalized.startswith("INTER"):
        return "I"
    if normalized.startswith("WET"):
        return "W"
    return normalized[:1] if normalized and normalized != "-" else "-"


def numeric_value(value: object) -> float | None:
    try:
        if value != value:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def fastf1_qualifying_section(session: object, title: str) -> dict | None:
    results = getattr(session, "results", None)
    if results is None or results.empty:
        return None
    rows = []
    for _, item in results.sort_values("Position", na_position="last").iterrows():
        rows.append(
            [
                format_position(item.get("Position")),
                fastf1_driver_code(item),
                fastf1_driver_number(item),
                fastf1_driver_full_name(item),
                str(item.get("TeamName") or "-"),
                format_timedelta(item.get("Q1")),
                format_timedelta(item.get("Q2")),
                format_timedelta(item.get("Q3")),
            ]
        )
    if not rows:
        return None
    return {
        "title": title,
        "columns": ["Pos", "DRV", "No", "Driver", "Team", "SQ1", "SQ2", "SQ3"],
        "rows": rows,
    }


def fastf1_practice_section(session: object, title: str) -> dict | None:
    laps = getattr(session, "laps", None)
    if laps is None or laps.empty:
        return None
    quick_laps = laps.pick_quicklaps()
    if quick_laps.empty:
        quick_laps = laps
    best_laps = quick_laps.sort_values("LapTime").groupby("DriverNumber", as_index=False).first().sort_values("LapTime")
    driver_names = fastf1_driver_names_by_code(session)

    rows = []
    for position, (_, item) in enumerate(best_laps.iterrows(), start=1):
        driver_code_value = fastf1_lap_driver_code(item)
        rows.append(
            [
                str(position),
                driver_code_value,
                fastf1_lap_driver_number(item),
                driver_names.get(driver_code_value, fastf1_lap_driver_name(item)),
                str(item.get("Team") or "-"),
                format_timedelta(item.get("LapTime")),
                format_gap(item.get("LapTime"), best_laps.iloc[0].get("LapTime")),
                format_timedelta(item.get("Sector1Time")),
                format_timedelta(item.get("Sector2Time")),
                format_timedelta(item.get("Sector3Time")),
                format_number(item.get("LapNumber")),
                str(item.get("Compound") or "-"),
            ]
        )
    if not rows:
        return None
    return {
        "title": title,
        "columns": ["Pos", "DRV", "No", "Driver", "Team", "Best lap", "Gap", "S1", "S2", "S3", "Lap", "Tyre"],
        "rows": rows,
    }


def fastf1_driver_names_by_code(session: object) -> dict[str, str]:
    results = getattr(session, "results", None)
    if results is None or results.empty:
        return {}
    names: dict[str, str] = {}
    for _, item in results.iterrows():
        code = fastf1_driver_code(item)
        if code:
            names[code] = fastf1_driver_full_name(item)
    return names


def fastf1_summary(session: object, alpha_data: dict | None) -> str:
    if alpha_data:
        return f"{alpha_session_summary(alpha_data)}. Timing details from FastF1."
    event = getattr(session, "event", {})
    event_name = event.get("EventName", "Formula 1 Grand Prix") if hasattr(event, "get") else "Formula 1 Grand Prix"
    session_name = getattr(session, "name", "Formula 1 session")
    return f"{session_name} for {event_name}. Timing details from FastF1."


def fastf1_facts(session: object, alpha_data: dict | None) -> list[dict]:
    if alpha_data:
        facts = alpha_session_facts(alpha_data)
    else:
        event = getattr(session, "event", {})
        facts = [
            {"label": "Session", "value": getattr(session, "name", "-")},
            {"label": "Season", "value": str(event.get("EventDate", "-").year) if hasattr(event.get("EventDate", None), "year") else "-"},
            {"label": "Round", "value": str(event.get("RoundNumber", "-")) if hasattr(event, "get") else "-"},
        ]
    facts.append({"label": "Timing source", "value": "FastF1"})
    return facts


def fastf1_driver_name(item: object) -> str:
    abbreviation = fastf1_driver_code(item)
    full_name = fastf1_driver_full_name(item)
    if abbreviation and full_name:
        return f"{abbreviation} - {full_name}"
    return str(full_name or abbreviation or "Unknown driver")


def fastf1_driver_code(item: object) -> str:
    return str(item.get("Abbreviation") or item.get("BroadcastName") or "")


def fastf1_driver_number(item: object) -> str:
    return str(item.get("DriverNumber") or item.name or "-")


def fastf1_driver_full_name(item: object) -> str:
    return str(item.get("FullName") or "Unknown driver")


def fastf1_lap_driver_code(item: object) -> str:
    return str(item.get("Driver") or "")


def fastf1_lap_driver_number(item: object) -> str:
    return format_number(item.get("DriverNumber"))


def fastf1_lap_driver_name(item: object) -> str:
    driver = item.get("Driver")
    driver_number = item.get("DriverNumber")
    return f"#{driver_number}" if driver_number else str(driver or "Unknown driver")


def format_position(value: object) -> str:
    try:
        if value != value:
            return "-"
        return str(int(value))
    except (TypeError, ValueError):
        return str(value or "-")


def format_number(value: object) -> str:
    try:
        if value != value:
            return "-"
        number = float(value)
        return str(int(number)) if number.is_integer() else str(number)
    except (TypeError, ValueError):
        return str(value or "-")


def format_gap(value: object, leader: object) -> str:
    try:
        gap = value - leader
        total = gap.total_seconds()
    except Exception:
        return "-"
    if total <= 0:
        return "-"
    return f"+{total:.3f}s"


def format_timedelta(value: object) -> str:
    if value is None:
        return "-"
    try:
        if value != value:
            return "-"
        total_seconds = value.total_seconds()
    except Exception:
        return str(value)
    minutes = int(total_seconds // 60)
    seconds = total_seconds - minutes * 60
    return f"{minutes}:{seconds:06.3f}" if minutes else f"{seconds:.3f}"
