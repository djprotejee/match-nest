from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.models import EntityKind, Event, EventStatus, F1Session, Follow, FollowLevel, Sport
from app.storage import (
    authenticate_user,
    create_session,
    create_custom_entity,
    create_user,
    delete_or_hide_entity_for_user,
    get_entity_record,
    list_events,
    mark_provider_fetch,
    preferences_for_user,
    provider_fetch_state,
    provider_fetched_at,
    set_user_f1_sessions,
    set_user_follow,
    translate_sql_for_postgres,
    update_custom_entity,
    upsert_events,
    user_for_session,
    verify_email,
)


class StorageTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
