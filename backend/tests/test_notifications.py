from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from app.models import Event, EventStatus, NotificationRule, Sport
from app.notifications import notification_payload


class NotificationTests(unittest.TestCase):
    def test_notification_payload_does_not_include_live_score(self) -> None:
        now = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)
        rule = NotificationRule(
            id="rule-cs2",
            user_id=1,
            name="CS2",
            enabled=True,
            target_type="sport",
            target_id="cs2",
            minutes_before=5,
            created_at=now,
            updated_at=now,
        )
        event = Event(
            id="cs2-live-score",
            title="Natus Vincere vs TheMongolz",
            sport=Sport.CS2,
            starts_at=now,
            status=EventStatus.LIVE,
            entity_ids=["navi"],
            source="pandascore",
            competition="IEM Cologne Major 2026",
            result_summary="1-1",
        )

        payload = notification_payload(rule, event)

        self.assertNotIn("1-1", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
