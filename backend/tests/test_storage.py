from __future__ import annotations

import tempfile
import time
import unittest
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.models import EntityKind, Event, EventStatus, F1Session, Follow, FollowLevel, Sport
from app.storage import (
    LibsqlConnection,
    authenticate_user,
    create_session,
    create_custom_entity,
    create_user,
    delete_stale_events_for_source,
    event_details_cache_state,
    delete_or_hide_entity_for_user,
    get_cached_event_details,
    get_cached_provider_payload,
    get_entity_record,
    get_event_provider_binding,
    list_events,
    mark_provider_fetch,
    preferences_for_user,
    provider_payload_state,
    provider_fetch_state,
    provider_fetched_at,
    set_user_f1_sessions,
    set_user_follow,
    translate_sql_for_postgres,
    update_custom_entity,
    upsert_event_details_cache,
    upsert_event_provider_binding,
    upsert_provider_payload_cache,
    upsert_events,
    user_for_session,
    verify_email,
)


class _FakeLibsqlCursor:
    description = [("value",)]
    lastrowid = None

    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list[dict[str, str]]:
        return [{"value": "ok"}]


class _FakeLibsqlConnection:
    def __init__(self, fail_first: bool = False) -> None:
        self.fail_first = fail_first
        self.execute_calls = 0
        self.closed = False
        self.commit_calls = 0

    def execute(self, sql: str, params: tuple[object, ...]) -> _FakeLibsqlCursor:
        self.execute_calls += 1
        if self.fail_first:
            self.fail_first = False
            raise ValueError('Hrana: `api error: `status=404 Not Found, body={"error":"stream not found: 3e99ddfc:22c15e"}``')
        return _FakeLibsqlCursor()

    def commit(self) -> None:
        self.commit_calls += 1

    def close(self) -> None:
        self.closed = True


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env_patch = patch.dict(os.environ, {"DATABASE_URL": "", "TURSO_DATABASE_URL": ""})
        self.env_patch.start()

    def tearDown(self) -> None:
        self.env_patch.stop()

    def test_events_roundtrip_through_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.storage.DB_PATH", Path(temp_dir) / "matchnest.sqlite"):
                event = Event(
                    id="football-1",
                    title="FC Barcelona vs Real Madrid CF",
                    sport=Sport.FOOTBALL,
                    starts_at=datetime(2026, 9, 25, 19, 0, tzinfo=timezone.utc),
                    status=EventStatus.UPCOMING,
                    entity_ids=["barcelona"],
                    source="football-data",
                    competition="La Liga",
                    importance=90,
                )

                upsert_events([event])
                stored = list_events(
                    datetime(2026, 9, 1, tzinfo=timezone.utc),
                    datetime(2026, 10, 1, tzinfo=timezone.utc),
                )

                self.assertEqual(len(stored), 1)
                self.assertEqual(stored[0].id, "football-1")
                self.assertEqual(stored[0].entity_ids, ["barcelona"])

    def test_provider_fetch_timestamp_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.storage.DB_PATH", Path(temp_dir) / "matchnest.sqlite"):
                mark_provider_fetch("FootballDataProvider", "2026-09", "ok")

                self.assertIsNotNone(provider_fetched_at("FootballDataProvider", "2026-09"))

    def test_provider_fetch_state_keeps_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.storage.DB_PATH", Path(temp_dir) / "matchnest.sqlite"):
                mark_provider_fetch("FootballDataProvider", "2026-09", "error", "HTTP 429")

                state = provider_fetch_state("FootballDataProvider", "2026-09")

                self.assertIsNotNone(state)
                self.assertEqual(state.status, "error")

    def test_event_details_cache_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.storage.DB_PATH", Path(temp_dir) / "matchnest.sqlite"):
                details = {
                    "event_id": "f1-2026-7-race",
                    "sport": "formula",
                    "source": "fastf1",
                    "summary": "Race classification",
                    "facts": [{"label": "Round", "value": "7"}],
                    "sections": [{"title": "Classification", "columns": ["Pos", "DRV"], "rows": [["1", "LEC"]]}],
                }

                upsert_event_details_cache("f1-2026-7-race", "fastf1", details)

                self.assertEqual(get_cached_event_details("f1-2026-7-race"), details)
                state = event_details_cache_state("f1-2026-7-race")
                self.assertEqual(state.provider, "fastf1")
                self.assertEqual(state.details, details)

    def test_event_details_cache_refreshes_fetched_at(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.storage.DB_PATH", Path(temp_dir) / "matchnest.sqlite"):
                details = {
                    "event_id": "cs2-1513136",
                    "sport": "cs2",
                    "source": "pandascore",
                    "summary": "Initial",
                    "facts": [],
                    "sections": [],
                }

                upsert_event_details_cache("cs2-1513136", "pandascore", details)
                first = event_details_cache_state("cs2-1513136")
                time.sleep(0.01)
                upsert_event_details_cache("cs2-1513136", "pandascore", {**details, "summary": "Updated"})
                second = event_details_cache_state("cs2-1513136")

                self.assertGreater(second.fetched_at, first.fetched_at)
                self.assertEqual(second.details["summary"], "Updated")

    def test_provider_payload_cache_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.storage.DB_PATH", Path(temp_dir) / "matchnest.sqlite"):
                payload = {"series": {"id": 2589176}, "maps": [{"id": 1}]}

                upsert_provider_payload_cache("grid", "end-state:grid:series:2589176", payload)

                self.assertEqual(get_cached_provider_payload("grid", "end-state:grid:series:2589176"), payload)
                self.assertIsNotNone(provider_payload_state("grid", "end-state:grid:series:2589176").fetched_at)

    def test_provider_payload_cache_refreshes_fetched_at(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.storage.DB_PATH", Path(temp_dir) / "matchnest.sqlite"):
                upsert_provider_payload_cache("api-football", "fixtures:id=42", {"version": 1})
                first = provider_payload_state("api-football", "fixtures:id=42")
                time.sleep(0.01)
                upsert_provider_payload_cache("api-football", "fixtures:id=42", {"version": 2})
                second = provider_payload_state("api-football", "fixtures:id=42")

                self.assertGreater(second.fetched_at, first.fetched_at)
                self.assertEqual(second.payload, {"version": 2})

    def test_event_provider_binding_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.storage.DB_PATH", Path(temp_dir) / "matchnest.sqlite"):
                event = Event(
                    id="cs2-1513136",
                    title="Natus Vincere vs TheMongolz",
                    sport=Sport.CS2,
                    starts_at=datetime(2026, 6, 11, 9, 0, tzinfo=timezone.utc),
                    status=EventStatus.PAST,
                    entity_ids=["navi_cs2"],
                    source="pandascore",
                    competition="IEM Cologne Major 2026",
                )

                upsert_events([event])
                upsert_event_provider_binding(
                    "cs2-1513136",
                    "grid",
                    "series_id",
                    "2589176",
                    confidence=0.98,
                    metadata={"matched_by": "team-time-tournament"},
                )

                self.assertEqual(get_event_provider_binding("cs2-1513136", "grid", "series_id"), "2589176")

    def test_delete_stale_events_for_source_keeps_current_provider_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.storage.DB_PATH", Path(temp_dir) / "matchnest.sqlite"):
                old_event = Event(
                    id="f4-italian-2026-monza",
                    title="Italian F4 - Monza",
                    sport=Sport.FORMULA,
                    starts_at=datetime(2026, 6, 19, 10, 0, tzinfo=timezone.utc),
                    status=EventStatus.UPCOMING,
                    entity_ids=["bondarev", "italian_f4"],
                    source="f4-calendar",
                    competition="Italian F4 Championship",
                )
                current_event = Event(
                    id="f4-italian-2026-monza-day-1",
                    title="Italian F4 - Monza Day 1",
                    sport=Sport.FORMULA,
                    starts_at=datetime(2026, 6, 19, 10, 0, tzinfo=timezone.utc),
                    status=EventStatus.UPCOMING,
                    entity_ids=["bondarev", "italian_f4"],
                    source="f4-calendar",
                    competition="Italian F4 Championship",
                )
                other_source_event = Event(
                    id="f1-2026-9-race",
                    title="Canadian Grand Prix",
                    sport=Sport.FORMULA,
                    starts_at=datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc),
                    status=EventStatus.UPCOMING,
                    entity_ids=["f1"],
                    source="jolpica",
                    competition="Formula 1",
                )

                upsert_events([old_event, current_event, other_source_event])
                delete_stale_events_for_source(
                    "f4-calendar",
                    datetime(2026, 6, 1, tzinfo=timezone.utc),
                    datetime(2026, 7, 1, tzinfo=timezone.utc),
                    ["f4-italian-2026-monza-day-1"],
                )

                ids = {event.id for event in list_events()}
                self.assertNotIn("f4-italian-2026-monza", ids)
                self.assertIn("f4-italian-2026-monza-day-1", ids)
                self.assertIn("f1-2026-9-race", ids)

    def test_user_registration_verification_and_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.storage.DB_PATH", Path(temp_dir) / "matchnest.sqlite"):
                user, verification_token = create_user("User@Example.com", "correct horse battery")

                self.assertEqual(user.email, "user@example.com")
                self.assertIsNone(user.email_verified_at)
                self.assertIsNotNone(authenticate_user("user@example.com", "correct horse battery"))

                verified_user = verify_email(verification_token)
                self.assertIsNotNone(verified_user)
                self.assertTrue(verified_user.is_email_verified)

                session_token = create_session(verified_user.id)
                session_user = user_for_session(session_token)

                self.assertIsNotNone(session_user)
                self.assertEqual(session_user.email, "user@example.com")

    def test_user_preferences_are_scoped_per_account(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.storage.DB_PATH", Path(temp_dir) / "matchnest.sqlite"):
                first_user, _ = create_user("first@example.com", "correct horse battery")
                second_user, _ = create_user("second@example.com", "correct horse battery")

                set_user_follow(first_user.id, Follow("ferrari", FollowLevel.MAIN))
                set_user_f1_sessions(first_user.id, [F1Session.RACE, F1Session.PRACTICE])

                first_preferences = preferences_for_user(first_user.id)
                second_preferences = preferences_for_user(second_user.id)

                self.assertEqual(first_preferences.follows["ferrari"].level, FollowLevel.MAIN)
                self.assertEqual(second_preferences.follows["ferrari"].level, FollowLevel.STARRED)
                self.assertEqual(first_preferences.f1_sessions, {F1Session.RACE, F1Session.PRACTICE})

    def test_user_preferences_fall_back_when_sessions_payload_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.storage.DB_PATH", Path(temp_dir) / "matchnest.sqlite"):
                user, _ = create_user("broken-settings@example.com", "correct horse battery")

                import sqlite3

                connection = sqlite3.connect(Path(temp_dir) / "matchnest.sqlite")
                try:
                    connection.execute(
                        "UPDATE user_settings SET f1_sessions = ?, timezone = ? WHERE user_id = ?",
                        ('["race","broken-session"]', "", user.id),
                    )
                    connection.commit()
                finally:
                    connection.close()

                preferences = preferences_for_user(user.id)

                self.assertEqual(preferences.f1_sessions, {F1Session.RACE, F1Session.QUALIFYING, F1Session.SPRINT})
                self.assertEqual(preferences.timezone, "Europe/Kyiv")

    def test_custom_entity_can_be_updated_and_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.storage.DB_PATH", Path(temp_dir) / "matchnest.sqlite"):
                user, _ = create_user("custom@example.com", "correct horse battery")
                created = create_custom_entity(
                    user.id,
                    "Test Team",
                    Sport.CS2,
                    kind=EntityKind.TEAM,
                    color="#F4B740",
                    level=FollowLevel.STARRED,
                    aliases=["TT"],
                )

                updated = update_custom_entity(
                    user.id,
                    created.entity.id,
                    "Better Team",
                    Sport.CS2,
                    kind=EntityKind.TEAM,
                    color="#F4B740",
                    aliases=["BT"],
                    bindings=[],
                )
                result = delete_or_hide_entity_for_user(user.id, updated.entity.id)

                self.assertEqual(updated.entity.name, "Better Team")
                self.assertEqual(result, "deleted")
                self.assertIsNone(get_entity_record(updated.entity.id))

    def test_seed_entity_delete_hides_for_user(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.storage.DB_PATH", Path(temp_dir) / "matchnest.sqlite"):
                user, _ = create_user("seed@example.com", "correct horse battery")

                result = delete_or_hide_entity_for_user(user.id, "barcelona")
                preferences = preferences_for_user(user.id)

                self.assertEqual(result, "hidden")
                self.assertEqual(preferences.follows["barcelona"].level, FollowLevel.HIDDEN)

    def test_postgres_sql_translation_handles_storage_dialect(self) -> None:
        ddl, wants_lastrowid = translate_sql_for_postgres(
            "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT)"
        )
        insert_ignore, _ = translate_sql_for_postgres(
            "INSERT OR IGNORE INTO entity_aliases (entity_id, alias) VALUES (?, ?)"
        )
        user_insert, user_wants_lastrowid = translate_sql_for_postgres(
            "INSERT INTO users (email, password_hash, created_at, updated_at) VALUES (?, ?, ?, ?)"
        )

        self.assertIn("GENERATED BY DEFAULT AS IDENTITY", ddl)
        self.assertFalse(wants_lastrowid)
        self.assertEqual(
            insert_ignore,
            "INSERT INTO entity_aliases (entity_id, alias) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        )
        self.assertTrue(user_wants_lastrowid)
        self.assertTrue(user_insert.endswith("RETURNING id"))
        self.assertIn("VALUES (%s, %s, %s, %s)", user_insert)

    def test_libsql_connection_reconnects_after_transient_hrana_stream_error(self) -> None:
        first_connection = _FakeLibsqlConnection(fail_first=True)
        second_connection = _FakeLibsqlConnection()

        with patch("app.storage.libsql.connect", side_effect=[first_connection, second_connection]):
            connection = LibsqlConnection("libsql://matchnest.turso.io", "token")
            cursor = connection.execute("SELECT 1", ())

        self.assertTrue(first_connection.closed)
        self.assertEqual(first_connection.execute_calls, 1)
        self.assertEqual(second_connection.execute_calls, 1)
        self.assertEqual(cursor.fetchall(), [{"value": "ok"}])


if __name__ == "__main__":
    unittest.main()
