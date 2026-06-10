from __future__ import annotations

import unittest
from datetime import datetime

from app.models import EventStatus, F1Session, FollowLevel, KYIV_TZ, Sport
from app.seed import DEFAULT_PREFERENCES, demo_events
from app.service import filter_events, month_range, serialize_event, visible_follow_level


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

    def test_spoiler_result_can_be_revealed(self) -> None:
        event = next(item for item in self.events if item.id == "barca-past-demo")
        payload = serialize_event(event, DEFAULT_PREFERENCES, reveal_spoilers=True)
        self.assertFalse(payload["result_hidden"])
        self.assertEqual(payload["result_summary"], "Barcelona 2-1 Real Madrid")

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


if __name__ == "__main__":
    unittest.main()

