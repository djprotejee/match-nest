from __future__ import annotations

import unittest

from app.providers.grid import parse_grid_series_ids


class GridProviderTests(unittest.TestCase):
    def test_parse_grid_series_ids_accepts_colon_and_equals(self) -> None:
        self.assertEqual(
            parse_grid_series_ids("cs2-101:2589176, cs2-202=2589177, broken"),
            {"cs2-101": "2589176", "cs2-202": "2589177"},
        )


if __name__ == "__main__":
    unittest.main()

