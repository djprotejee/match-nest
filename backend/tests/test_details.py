from __future__ import annotations

import unittest
from isolated_case import IsolatedTestCase

from app.details import normalize_event_details


class EventDetailsV2Tests(IsolatedTestCase):
    def test_normalize_event_details_preserves_legacy_shape_and_adds_v2_fields(self) -> None:
        details = {
            "event_id": "football-apifootball-42",
            "sport": "football",
            "source": "api-football",
            "summary": "Hungary 1-2 Ukraine",
            "facts": [],
            "sections": [
                {"title": "Score", "columns": ["Team", "Goals"], "rows": [["Ukraine", "2"]]},
                {"title": "Lineups", "columns": ["Team", "Player"], "rows": [["Ukraine", "Player"]]},
                {"title": "Match events", "columns": ["Time", "Type"], "rows": [["67'", "Goal"]]},
            ],
        }

        normalized = normalize_event_details(details, ["api-football:fixtures-id=42"])

        self.assertEqual(normalized["version"], 2)
        self.assertEqual(normalized["sections"][0]["title"], "Score")
        self.assertEqual(normalized["score"]["rows"][0], ["Ukraine", "2"])
        self.assertEqual(normalized["lineups"][0]["Player"], "Player")
        self.assertEqual(normalized["timeline_events"][0]["Type"], "Goal")
        self.assertEqual(normalized["raw_payload_cache_keys"], ["api-football:fixtures-id=42"])


if __name__ == "__main__":
    unittest.main()
