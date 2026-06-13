from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app.models import Event, EventStatus, F1Session, Sport
from app.providers.api_football import ApiFootballProvider, api_football_seasons, api_football_status
from app.providers.espn_football import EspnFootballProvider, espn_details_payload, month_keys
from app.providers.registry import (
    dedupe_cross_source_events,
    detail_error_summary,
    followed_football_competitions,
    followed_football_team_queries,
    provider_refresh_ttl,
    provider_should_refresh,
)
from app.providers.f4_calendar import F4CalendarProvider
from app.providers.f1_jolpica import parse_utc_datetime
from app.providers.football_data import (
    dedupe_matches,
    find_team_id,
    football_entity_ids,
    football_status,
    parse_entity_id_map,
    parse_entity_query_map,
)
from app.providers.hltv import find_hltv_team_rank, parse_hltv_rankings, rank_based_tier
from app.providers.thesportsdb import TheSportsDBFootballProvider, parse_thesportsdb_datetime
from app.providers.pandascore import (
    cs2_entity_ids,
    cs2_score_section,
    cs2_score_summary,
    cs2_status,
    dedupe_matches as dedupe_cs2_matches,
    event_in_range,
    pages_for_bucket,
    short_team_tag,
)


class ProviderMappingTests(unittest.TestCase):
    def test_f1_datetime_parser_uses_utc(self) -> None:
        parsed = parse_utc_datetime({"date": "2026-06-14", "time": "13:00:00Z"})

        self.assertEqual(parsed, datetime(2026, 6, 14, 13, 0, tzinfo=timezone.utc))

    def test_f4_weekend_is_split_into_day_events(self) -> None:
        provider = F4CalendarProvider()

        events = provider.fetch(
            start=datetime(2026, 6, 19, tzinfo=timezone.utc),
            end=datetime(2026, 6, 22, tzinfo=timezone.utc),
        )

        self.assertEqual([event.title for event in events], ["Italian F4 - Monza Day 1", "Italian F4 - Monza Day 2", "Italian F4 - Monza Day 3"])
        self.assertTrue(all(event.source == "f4-calendar" for event in events))
        self.assertEqual([event.starts_at.date().isoformat() for event in events], ["2026-06-19", "2026-06-20", "2026-06-21"])

    def test_f1_detail_errors_use_product_copy(self) -> None:
        summary = detail_error_summary("f1-2026-7-thirdpractice", Exception("HTTP Error 403: Forbidden"))

        self.assertNotIn("403", summary)
        self.assertNotIn("Forbidden", summary)
        self.assertIn("not available yet", summary)

    def test_football_maps_main_team_and_starred_competition(self) -> None:
        ids = football_entity_ids("Ukraine", "France", "UEFA Euro")

        self.assertIn("ukraine_nt", ids)
        self.assertIn("euro", ids)

    def test_football_does_not_map_espanyol_as_fc_barcelona(self) -> None:
        ids = football_entity_ids("RCD Espanyol de Barcelona", "Real Madrid CF", "La Liga")

        self.assertNotIn("barcelona", ids)

    def test_football_maps_exact_fc_barcelona(self) -> None:
        ids = football_entity_ids("FC Barcelona", "Real Madrid CF", "La Liga")

        self.assertIn("barcelona", ids)

    def test_football_live_status_maps_to_live(self) -> None:
        status = football_status("IN_PLAY", datetime(2026, 6, 14, 13, 0, tzinfo=timezone.utc))

        self.assertEqual(status, EventStatus.LIVE)

    def test_football_team_id_map_ignores_invalid_values(self) -> None:
        values = parse_entity_id_map("barcelona:81,broken,nope:x,ukraine_nt:794")

        self.assertEqual(values, {"barcelona": 81, "ukraine_nt": 794})

    def test_football_team_query_map_supports_aliases(self) -> None:
        values = parse_entity_query_map("barcelona:FC Barcelona|Barcelona,ukraine_nt:Ukraine")

        self.assertEqual(values["barcelona"], ["FC Barcelona", "Barcelona"])
        self.assertEqual(values["ukraine_nt"], ["Ukraine"])

    def test_football_team_discovery_matches_names(self) -> None:
        team_id = find_team_id(
            [
                {"id": 10, "name": "FC Barcelona", "shortName": "Barca", "tla": "FCB"},
                {"id": 11, "name": "Real Madrid CF", "shortName": "Madrid", "tla": "RMA"},
            ],
            ["FC Barcelona"],
        )

        self.assertEqual(team_id, 10)

    def test_football_dedupe_merges_entity_ids(self) -> None:
        matches = [
            {"id": 1, "_matchnest_entity_ids": ["barcelona"]},
            {"id": 1, "_matchnest_entity_ids": ["ucl"]},
        ]

        self.assertEqual(dedupe_matches(matches)[0]["_matchnest_entity_ids"], ["barcelona", "ucl"])

    def test_registry_builds_football_sources_from_followed_entities(self) -> None:
        from app.seed import DEFAULT_PREFERENCES

        self.assertIn("barcelona", followed_football_team_queries(DEFAULT_PREFERENCES))
        self.assertIn("ukraine_nt", followed_football_team_queries(DEFAULT_PREFERENCES))
        self.assertEqual(followed_football_competitions(DEFAULT_PREFERENCES), {"ucl": "CL", "world_cup": "WC", "euro": "EC"})

    def test_registry_retries_after_provider_error(self) -> None:
        from app.storage import mark_provider_fetch
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch

        with TemporaryDirectory() as temp_dir:
            with patch("app.storage.DB_PATH", Path(temp_dir) / "matchnest.sqlite"):
                mark_provider_fetch("FootballDataProvider", "range", "error", "HTTP 429")

                self.assertTrue(provider_should_refresh("FootballDataProvider", "range"))

    def test_espn_refreshes_current_window_more_often(self) -> None:
        now = datetime.now(timezone.utc)

        self.assertLess(
            provider_refresh_ttl("EspnFootballProvider", now, now),
            provider_refresh_ttl("EspnFootballProvider", datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 10, 1, tzinfo=timezone.utc)),
        )

    def test_hltv_ranking_parser_and_alias_lookup(self) -> None:
        html = """
        <div class="ranked-team standard-box">
          <span class="position wide-position">#2</span>
          <span class="name">Natus Vincere</span>
          <span class="points">(712<span class="gtSmartphone-only"> HLTV</span> points)</span>
        </div>
        <div class="ranked-team standard-box">
          <span class="position wide-position">#4</span>
          <span class="name">Falcons</span>
          <span class="points">(509<span class="gtSmartphone-only"> HLTV</span> points)</span>
        </div>
        """
        rankings = parse_hltv_rankings(html, "https://www.hltv.org/ranking/teams/2026/june/8")

        self.assertEqual(find_hltv_team_rank("NAVI", rankings).rank, 2)
        self.assertEqual(find_hltv_team_rank("Team Falcons", rankings).points, 509)
        self.assertEqual(rank_based_tier(4), "T1")

    def test_thesportsdb_maps_ukraine_senior_fixture(self) -> None:
        item = {
            "idEvent": "2442748",
            "dateEvent": "2026-09-25",
            "strTimestamp": "2026-09-25T18:45:00",
            "strHomeTeam": "Hungary",
            "strAwayTeam": "Ukraine",
            "strLeague": "UEFA Nations League",
            "strStatus": "NS",
        }
        provider = TheSportsDBFootballProvider(team_ids={"ukraine_nt": 133915})

        event = provider._event_from_payload(item, "ukraine_nt")

        self.assertIsNotNone(event)
        self.assertEqual(event.id, "football-tsdb-2442748")
        self.assertIn("ukraine_nt", event.entity_ids)
        self.assertEqual(event.source, "thesportsdb")
        self.assertEqual(parse_thesportsdb_datetime(item), datetime(2026, 9, 25, 18, 45, tzinfo=timezone.utc))

    def test_api_football_maps_followed_team_fixture(self) -> None:
        item = {
            "fixture": {
                "id": 42,
                "date": "2026-09-25T18:45:00+00:00",
                "status": {"short": "FT"},
            },
            "league": {"name": "UEFA Nations League"},
            "teams": {
                "home": {"name": "Hungary"},
                "away": {"name": "Ukraine"},
            },
            "goals": {"home": 1, "away": 2},
        }
        provider = ApiFootballProvider(token="test", team_queries={"ukraine_nt": ["Ukraine"]})

        event = provider._fixture_to_event(item, "ukraine_nt")

        self.assertIsNotNone(event)
        self.assertEqual(event.id, "football-apifootball-42")
        self.assertEqual(event.source, "api-football")
        self.assertIn("ukraine_nt", event.entity_ids)
        self.assertEqual(event.result_summary, "Hungary 1-2 Ukraine")

    def test_api_football_not_started_after_begin_time_is_delayed(self) -> None:
        status = api_football_status(
            {"short": "NS"},
            datetime(2020, 9, 25, 18, 45, tzinfo=timezone.utc),
        )

        self.assertEqual(status, EventStatus.DELAYED)

    def test_api_football_prefers_national_team_for_nt_query(self) -> None:
        provider = ApiFootballProvider(token="test", team_queries={"ukraine_nt": ["Ukraine NT", "Ukraine"]})

        with patch.object(
            provider,
            "_request",
            return_value={
                "response": [
                    {"team": {"id": 550, "name": "Shakhtar Donetsk", "country": "Ukraine", "national": False}},
                    {"team": {"id": 772, "name": "Ukraine", "country": "Ukraine", "national": True}},
                ]
            },
        ):
            self.assertEqual(provider.resolved_team_entities()["ukraine_nt"], 772)

    def test_api_football_seasons_include_required_calendar_years(self) -> None:
        self.assertEqual(
            api_football_seasons(datetime(2026, 9, 1).date(), datetime(2026, 10, 1).date()),
            [2026],
        )
        self.assertEqual(
            api_football_seasons(datetime(2027, 1, 1).date(), datetime(2027, 1, 31).date()),
            [2026, 2027],
        )

    def test_espn_maps_future_ukraine_fixture(self) -> None:
        item = {
            "id": "401861054",
            "date": "2026-09-25T18:45Z",
            "competitions": [
                {
                    "competitors": [
                        {"homeAway": "home", "team": {"id": "477", "displayName": "Hungary"}, "score": "0"},
                        {"homeAway": "away", "team": {"id": "457", "displayName": "Ukraine"}, "score": "0"},
                    ],
                }
            ],
            "status": {"type": {"state": "pre", "completed": False}},
        }
        provider = EspnFootballProvider(team_ids={"ukraine_nt": {"457"}})

        event = provider._event_from_payload(item, "uefa.nations")

        self.assertIsNotNone(event)
        self.assertEqual(event.id, "football-espn-uefa.nations-401861054")
        self.assertIn("ukraine_nt", event.entity_ids)
        self.assertEqual(event.source, "espn")

    def test_espn_details_include_match_events_when_available(self) -> None:
        payload = {
            "id": "401861054",
            "date": "2026-09-25T18:45Z",
            "competitions": [
                {
                    "venue": {"fullName": "Puskas Arena"},
                    "competitors": [
                        {"homeAway": "home", "team": {"id": "477", "displayName": "Hungary"}, "score": "1"},
                        {"homeAway": "away", "team": {"id": "457", "displayName": "Ukraine"}, "score": "2"},
                    ],
                    "details": [
                        {
                            "clock": {"displayValue": "67'"},
                            "team": {"displayName": "Ukraine"},
                            "type": {"text": "Goal"},
                            "text": "Goal by Ukraine",
                        }
                    ],
                }
            ],
            "status": {"type": {"state": "post", "completed": True, "description": "Final"}},
        }

        details = espn_details_payload("football-espn-uefa.nations-401861054", payload, "uefa.nations")

        self.assertEqual(details["sport"], "football")
        self.assertEqual(details["summary"], "Hungary 1-2 Ukraine")
        self.assertEqual(details["sections"][1]["rows"][0][2], "Goal")

    def test_espn_month_keys_cover_range(self) -> None:
        self.assertEqual(
            month_keys(datetime(2026, 9, 25, tzinfo=timezone.utc), datetime(2026, 11, 1, tzinfo=timezone.utc)),
            ["202609", "202610", "202611"],
        )

    def test_registry_dedupes_same_football_event_across_sources(self) -> None:
        starts_at = datetime(2026, 9, 25, 18, 45, tzinfo=timezone.utc)
        events = [
            Event(
                id="football-tsdb-1",
                title="Hungary vs Ukraine",
                sport=Sport.FOOTBALL,
                starts_at=starts_at,
                status=EventStatus.UPCOMING,
                entity_ids=["ukraine_nt"],
                source="thesportsdb",
            ),
            Event(
                id="football-espn-1",
                title="Hungary vs Ukraine",
                sport=Sport.FOOTBALL,
                starts_at=starts_at,
                status=EventStatus.UPCOMING,
                entity_ids=["ukraine_nt"],
                source="espn",
            ),
        ]

        deduped = dedupe_cross_source_events(events)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].source, "espn")

    def test_cs2_maps_navi_and_blast(self) -> None:
        ids = cs2_entity_ids("NAVI vs Vitality", "BLAST Premier")

        self.assertIn("navi_cs2", ids)
        self.assertIn("blast", ids)

    def test_cs2_does_not_map_navi_junior_as_main_navi(self) -> None:
        ids = cs2_entity_ids("NAVI Junior vs Spirit Academy", "European Pro League")

        self.assertNotIn("navi_cs2", ids)

    def test_cs2_missing_start_time_is_tbd(self) -> None:
        status = cs2_status("not_started", None)

        self.assertEqual(status, EventStatus.TBD)

    def test_cs2_not_started_after_begin_time_is_delayed(self) -> None:
        status = cs2_status("not_started", datetime(2020, 6, 11, 12, 0, tzinfo=timezone.utc))

        self.assertEqual(status, EventStatus.DELAYED)

    def test_cs2_not_started_before_begin_time_stays_upcoming(self) -> None:
        status = cs2_status("not_started", datetime(2099, 6, 11, 12, 0, tzinfo=timezone.utc))

        self.assertEqual(status, EventStatus.UPCOMING)

    def test_cs2_dedupe_keeps_first_match_by_id(self) -> None:
        matches = [{"id": 10, "name": "A"}, {"id": 10, "name": "B"}, {"id": 11, "name": "C"}]

        self.assertEqual([item["name"] for item in dedupe_cs2_matches(matches)], ["A", "C"])

    def test_cs2_score_summary_uses_opponent_order(self) -> None:
        item = {
            "status": "finished",
            "opponents": [
                {"opponent": {"id": 1, "name": "NAVI"}},
                {"opponent": {"id": 2, "name": "Spirit"}},
            ],
            "results": [{"team_id": 2, "score": 1}, {"team_id": 1, "score": 2}],
        }

        self.assertEqual(cs2_score_summary(item, "NAVI vs Spirit"), "2-1")

    def test_cs2_score_section_keeps_hltv_rank_separate(self) -> None:
        item = {
            "opponents": [
                {"opponent": {"id": 1, "name": "Natus Vincere", "acronym": "NAVI", "location": "UA"}},
                {"opponent": {"id": 2, "name": "Spirit", "acronym": "Spirit", "location": "RU"}},
            ],
            "results": [{"team_id": 1, "score": 2}, {"team_id": 2, "score": 1}],
            "winner_id": 1,
        }

        with patch("app.providers.pandascore.hltv_rankings", return_value={}):
            section = cs2_score_section(item)

        self.assertEqual(section["columns"], ["Tag", "Team", "Score", "HLTV", "HLTV pts", "Tier", "Country"])
        self.assertEqual(section["rows"][0][1], "Natus Vincere")

    def test_cs2_short_team_tag_keeps_common_tags(self) -> None:
        self.assertEqual(short_team_tag("Natus Vincere"), "NAVI")
        self.assertEqual(short_team_tag("Natus Vincere Junior"), "NAVI.J")

    def test_cs2_range_fetch_allows_deeper_past_pagination(self) -> None:
        start = datetime(2026, 6, 9, tzinfo=timezone.utc)
        end = datetime(2026, 6, 12, tzinfo=timezone.utc)

        self.assertEqual(pages_for_bucket("past", start, end), 20)

    def test_cs2_tbd_without_start_is_excluded_from_bounded_range(self) -> None:
        event = Event(
            id="cs2-1",
            title="TBD",
            sport=Sport.CS2,
            starts_at=None,
            status=EventStatus.TBD,
            entity_ids=["cs2_explore"],
            source="pandascore",
        )

        self.assertFalse(
            event_in_range(
                event,
                datetime(2026, 6, 11, tzinfo=timezone.utc),
                datetime(2026, 6, 12, tzinfo=timezone.utc),
            )
        )


if __name__ == "__main__":
    unittest.main()
