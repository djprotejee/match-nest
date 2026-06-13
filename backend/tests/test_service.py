from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from app.models import Event, EventStatus, F1Session, Follow, FollowLevel, KYIV_TZ, Sport, UserPreferences
from app.seed import DEFAULT_PREFERENCES, demo_events
from app.service import effective_event_status, filter_events, month_range, serialize_event, visible_follow_level
from app.main import spoiler_safe_event_details, tournament_snapshot_sections, tournament_summary


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 6, 10, 18, 0, tzinfo=KYIV_TZ)
        self.events = demo_events(self.now)

    def test_main_level_wins_for_navi_event(self) -> None:
        event = next(item for item in self.events if item.id == "navi-live-demo")
        self.assertEqual(visible_follow_level(event, DEFAULT_PREFERENCES), FollowLevel.MAIN)

    def test_f1_practice_hidden_by_default(self) -> None:
        filtered = filter_events(
            self.events,
            DEFAULT_PREFERENCES,
            sports={Sport.FORMULA},
        )
        ids = {event.id for event in filtered}
        self.assertIn("f1-race-demo", ids)
        self.assertNotIn("f1-practice-demo", ids)

    def test_spoiler_result_is_hidden_for_past_event(self) -> None:
        event = next(item for item in self.events if item.id == "barca-past-demo")
        payload = serialize_event(event, DEFAULT_PREFERENCES)
        self.assertTrue(payload["result_hidden"])
        self.assertIsNone(payload["result_summary"])

    def test_spoiler_result_is_hidden_for_live_event(self) -> None:
        event = Event(
            id="live-score",
            title="NAVI vs TheMongolz",
            sport=Sport.CS2,
            starts_at=datetime.now(KYIV_TZ),
            status=EventStatus.LIVE,
            entity_ids=["navi"],
            source="pandascore",
            result_summary="1-1",
        )
        payload = serialize_event(event, DEFAULT_PREFERENCES)
        self.assertTrue(payload["result_hidden"])
        self.assertIsNone(payload["result_summary"])

    def test_spoiler_result_can_be_revealed(self) -> None:
        event = next(item for item in self.events if item.id == "barca-past-demo")
        payload = serialize_event(event, DEFAULT_PREFERENCES, reveal_spoilers=True)
        self.assertFalse(payload["result_hidden"])
        self.assertEqual(payload["result_summary"], "Barcelona 2-1 Real Madrid")

    def test_past_only_spoiler_keeps_live_scores_visible(self) -> None:
        preferences = UserPreferences(default_hide_spoilers=True, ui_state={"spoilerMode": "past"})
        event = Event(
            id="live-score",
            title="NAVI vs TheMongolz",
            sport=Sport.CS2,
            starts_at=datetime.now(KYIV_TZ),
            status=EventStatus.LIVE,
            entity_ids=["navi"],
            source="pandascore",
            result_summary="1-1",
        )

        payload = serialize_event(event, preferences)

        self.assertFalse(payload["result_hidden"])
        self.assertEqual(payload["result_summary"], "1-1")

    def test_live_only_spoiler_hides_live_score_and_keeps_past_score_visible(self) -> None:
        preferences = UserPreferences(default_hide_spoilers=True, ui_state={"spoilerMode": "live"})
        live_event = Event(
            id="live-score",
            title="NAVI vs TheMongolz",
            sport=Sport.CS2,
            starts_at=datetime.now(KYIV_TZ),
            status=EventStatus.LIVE,
            entity_ids=["navi"],
            source="pandascore",
            result_summary="1-1",
        )
        past_event = Event(
            id="past-score",
            title="Barcelona vs Real Madrid",
            sport=Sport.FOOTBALL,
            starts_at=self.now,
            status=EventStatus.PAST,
            entity_ids=["barcelona"],
            source="espn",
            result_summary="2-1",
        )

        self.assertTrue(serialize_event(live_event, preferences)["result_hidden"])
        self.assertFalse(serialize_event(past_event, preferences)["result_hidden"])

    def test_spoiler_safe_event_details_hide_sensitive_sections(self) -> None:
        preferences = UserPreferences(default_hide_spoilers=True, ui_state={"spoilerMode": "all"})
        event = Event(
            id="cs2-score",
            title="NAVI vs TheMongolz",
            sport=Sport.CS2,
            starts_at=datetime.now(KYIV_TZ),
            status=EventStatus.LIVE,
            entity_ids=["navi_cs2"],
            source="pandascore",
            result_summary="1-1",
        )
        details = {
            "event_id": "cs2-score",
            "summary": "NAVI vs TheMongolz | 1-1",
            "sections": [
                {"title": "Match score", "columns": ["Team", "Score"], "rows": [["NAVI", "1"]]},
                {"title": "Lineups", "columns": ["Team", "Player"], "rows": [["NAVI", "b1t"]]},
                {"title": "Player statistics", "columns": ["Player", "K"], "rows": [["b1t", "22"]]},
            ],
            "score": {"title": "Match score", "columns": ["Team", "Score"], "rows": [["NAVI", "1"]]},
            "player_stats": [{"Player": "b1t", "K": "22"}],
        }

        safe_details = spoiler_safe_event_details(details, event, preferences)

        self.assertEqual(safe_details["summary"], "Details are hidden by spoiler mode for this event.")
        self.assertIsNone(safe_details["score"])
        self.assertEqual(safe_details["player_stats"], [])
        self.assertEqual([section["title"] for section in safe_details["sections"]], ["Spoiler hidden", "Lineups"])

    def test_elapsed_f1_qualifying_is_marked_past(self) -> None:
        event = Event(
            id="f1-qualifying",
            title="Barcelona Grand Prix - Qualifying",
            sport=Sport.FORMULA,
            starts_at=self.now,
            status=EventStatus.UPCOMING,
            entity_ids=["formula_1"],
            source="jolpica",
            session_type=F1Session.QUALIFYING,
        )

        self.assertEqual(effective_event_status(event, self.now + timedelta(hours=4)), EventStatus.PAST)

    def test_custom_spoiler_can_target_sport_category_or_entity(self) -> None:
        event = Event(
            id="barca-score",
            title="Barcelona vs Real Madrid",
            sport=Sport.FOOTBALL,
            starts_at=self.now,
            status=EventStatus.PAST,
            entity_ids=["barcelona"],
            source="espn",
            result_summary="2-1",
        )
        preferences = UserPreferences(
            follows={"barcelona": Follow("barcelona", "main")},
            default_hide_spoilers=True,
            ui_state={"spoilerMode": "custom", "spoilerSports": {}, "spoilerLevels": {"main": True}, "spoilerEntities": {}},
        )

        payload = serialize_event(event, preferences)

        self.assertTrue(payload["result_hidden"])
        self.assertIsNone(payload["result_summary"])

    def test_calendar_supports_previous_month(self) -> None:
        start, end = month_range(2026, 5)
        filtered = filter_events(
            self.events,
            DEFAULT_PREFERENCES,
            start=start,
            end=end,
            statuses={EventStatus.PAST},
        )
        self.assertEqual([event.id for event in filtered], ["ucl-prev-month-demo"])

    def test_calendar_supports_next_month(self) -> None:
        start, end = month_range(2026, 7)
        filtered = filter_events(self.events, DEFAULT_PREFERENCES, start=start, end=end)
        self.assertEqual([event.id for event in filtered], ["major-next-month-demo"])

    def test_practice_can_be_enabled(self) -> None:
        DEFAULT_PREFERENCES.f1_sessions.add(F1Session.PRACTICE)
        try:
            filtered = filter_events(
                self.events,
                DEFAULT_PREFERENCES,
                sports={Sport.FORMULA},
            )
            ids = {event.id for event in filtered}
            self.assertIn("f1-practice-demo", ids)
        finally:
            DEFAULT_PREFERENCES.f1_sessions.discard(F1Session.PRACTICE)

    def test_tournament_summary_groups_by_competition(self) -> None:
        events = [
            Event(
                id="football-1",
                title="Hungary vs Ukraine",
                sport=Sport.FOOTBALL,
                starts_at=self.now,
                status=EventStatus.UPCOMING,
                entity_ids=["ukraine_nt"],
                source="espn",
                competition="UEFA Nations League",
                importance=86,
            ),
            Event(
                id="football-2",
                title="Ukraine vs Iceland",
                sport=Sport.FOOTBALL,
                starts_at=self.now + timedelta(days=3),
                status=EventStatus.UPCOMING,
                entity_ids=["ukraine_nt"],
                source="espn",
                competition="UEFA Nations League",
                importance=80,
            ),
        ]
        preferences = UserPreferences(follows={"ukraine_nt": Follow("ukraine_nt", "main")})

        summary = tournament_summary(events, preferences)

        self.assertEqual(summary["name"], "UEFA Nations League")
        self.assertEqual(summary["sport"], "football")
        self.assertEqual(summary["event_count"], 2)
        self.assertEqual(summary["follow_level"], "main")

    def test_tournament_snapshot_has_product_fallback(self) -> None:
        event = Event(
            id="football-no-details",
            title="Hungary vs Ukraine",
            sport=Sport.FOOTBALL,
            starts_at=self.now,
            status=EventStatus.UPCOMING,
            entity_ids=["ukraine_nt"],
            source="espn",
            competition="UEFA Nations League",
        )

        sections = tournament_snapshot_sections([event], UserPreferences(), False)

        self.assertEqual(sections[0]["title"], "Tournament snapshot")

    def test_api_payload_can_include_practice_for_client_filtering(self) -> None:
        filtered = filter_events(
            self.events,
            DEFAULT_PREFERENCES,
            sports={Sport.FORMULA},
            apply_f1_session_filter=False,
        )
        ids = {event.id for event in filtered}
        self.assertIn("f1-race-demo", ids)
        self.assertIn("f1-practice-demo", ids)


if __name__ == "__main__":
    unittest.main()
