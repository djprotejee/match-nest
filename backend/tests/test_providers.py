from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.models import EventStatus, F1Session
from app.providers.f1_jolpica import parse_utc_datetime
from app.providers.football_data import football_entity_ids, football_status
from app.providers.pandascore import cs2_entity_ids, cs2_status


class ProviderMappingTests(unittest.TestCase):
    def test_f1_datetime_parser_uses_utc(self) -> None:
        parsed = parse_utc_datetime({"date": "2026-06-14", "time": "13:00:00Z"})

        self.assertEqual(parsed, datetime(2026, 6, 14, 13, 0, tzinfo=timezone.utc))

    def test_football_maps_main_team_and_starred_competition(self) -> None:
        ids = football_entity_ids("Ukraine", "France", "UEFA Euro")

        self.assertIn("ukraine_nt", ids)
        self.assertIn("euro", ids)

    def test_football_live_status_maps_to_live(self) -> None:
        status = football_status("IN_PLAY", datetime(2026, 6, 14, 13, 0, tzinfo=timezone.utc))

        self.assertEqual(status, EventStatus.LIVE)

    def test_cs2_maps_navi_and_blast(self) -> None:
        ids = cs2_entity_ids("NAVI vs Vitality", "BLAST Premier")

        self.assertIn("navi_cs2", ids)
        self.assertIn("blast", ids)

    def test_cs2_missing_start_time_is_tbd(self) -> None:
        status = cs2_status("not_started", None)

        self.assertEqual(status, EventStatus.TBD)


if __name__ == "__main__":
    unittest.main()

