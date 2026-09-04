from __future__ import annotations

import unittest
from isolated_case import IsolatedTestCase
from datetime import datetime, timedelta, timezone

from app.providers.pandascore import can_use_stable_pandascore_cache
from app.providers.grid import grid_payload_stat_sections, parse_grid_series_ids


class ProviderCacheTests(IsolatedTestCase):
    def test_parse_grid_series_ids_accepts_colon_and_equals(self) -> None:
        self.assertEqual(
            parse_grid_series_ids("cs2-101:2589176, cs2-202=2589177, broken"),
            {"cs2-101": "2589176", "cs2-202": "2589177"},
        )

    def test_recent_pandascore_past_cache_still_refreshes(self) -> None:
        self.assertFalse(can_use_stable_pandascore_cache("past", datetime.now(timezone.utc) - timedelta(hours=3)))
        self.assertTrue(can_use_stable_pandascore_cache("past", datetime.now(timezone.utc) - timedelta(days=3)))

    def test_grid_payload_stat_sections_extract_maps_teams_and_players(self) -> None:
        payload = {
            "maps": [
                {
                    "mapNumber": 1,
                    "mapName": "Mirage",
                    "score": {"team1": 13, "team2": 10},
                    "winnerTeam": {"name": "Natus Vincere"},
                    "duration": 2744,
                }
            ],
            "teams": [
                {"teamName": "Natus Vincere", "score": 2, "stats": {"kills": 80, "deaths": 70, "assists": 18}},
            ],
            "players": [
                {
                    "nickname": "b1t",
                    "teamName": "Natus Vincere",
                    "stats": {"kills": 22, "deaths": 14, "assists": 6, "adr": 91.4, "rating": 1.31},
                }
            ],
        }

        sections = grid_payload_stat_sections(payload)
        titles = [section["title"] for section in sections]

        self.assertIn("GRID maps", titles)
        self.assertIn("GRID team statistics", titles)
        self.assertIn("GRID player statistics", titles)


if __name__ == "__main__":
    unittest.main()
