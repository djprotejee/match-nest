from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.providers.pandascore import can_use_stable_pandascore_cache
from app.providers.grid import parse_grid_series_ids


class ProviderCacheTests(unittest.TestCase):
    def test_parse_grid_series_ids_accepts_colon_and_equals(self) -> None:
        self.assertEqual(
            parse_grid_series_ids("cs2-101:2589176, cs2-202=2589177, broken"),
            {"cs2-101": "2589176", "cs2-202": "2589177"},
        )

    def test_recent_pandascore_past_cache_still_refreshes(self) -> None:
        self.assertFalse(can_use_stable_pandascore_cache("past", datetime.now(timezone.utc) - timedelta(hours=3)))
        self.assertTrue(can_use_stable_pandascore_cache("past", datetime.now(timezone.utc) - timedelta(days=3)))


if __name__ == "__main__":
    unittest.main()
