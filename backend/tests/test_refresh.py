from concurrent.futures import Future
from datetime import datetime, timezone
from io import BytesIO
from threading import Thread
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
import json

from isolated_case import IsolatedTestCase
from app.models import Event, EventStatus, Sport
from app.providers.football_data import FootballDataProvider
from app.providers.pandascore import PandaScoreCS2Provider
from app.providers.espn_football import EspnFootballProvider
from app.providers import registry
from app.seed import DEFAULT_PREFERENCES
from app.storage import list_events, provider_fetch_state


class RefreshTests(IsolatedTestCase):
    def test_barcelona_survives_restricted_national_team_and_competitions(self):
        provider = FootballDataProvider(token="test", team_entities={"ukraine_nt": 794, "barcelona": 81})
        match = {"id": 564659, "utcDate": "2026-09-06T14:15:00Z", "homeTeam": {"name": "Valencia CF"}, "awayTeam": {"name": "FC Barcelona"}, "competition": {"name": "Primera Division"}, "status": "SCHEDULED"}
        def team_matches(team_id, *args):
            if team_id == 794:
                raise HTTPError("https://example.com", 403, "Forbidden", {}, None)
            return [match]
        with patch.object(provider, "_fetch_team_matches", side_effect=team_matches), patch.object(provider, "_fetch_competition_matches", side_effect=RuntimeError("unavailable")):
            events = registry.refresh_provider(provider, "FootballDataProvider", "regression", datetime(2026,9,1,tzinfo=timezone.utc), datetime(2026,10,1,tzinfo=timezone.utc))
        self.assertEqual([e.id for e in events], ["football-564659"])
        self.assertEqual(list_events()[0].id, "football-564659")
        self.assertEqual(provider_fetch_state("FootballDataProvider", "regression").status, "error")

    def test_failed_pandascore_request_is_not_marked_successful(self):
        provider = PandaScoreCS2Provider(token="test")
        with patch("app.providers.pandascore.urlopen", side_effect=OSError("offline")):
            registry.refresh_provider(provider, "PandaScoreCS2Provider", "offline", None, None)
        self.assertEqual(provider_fetch_state("PandaScoreCS2Provider", "offline").status, "error")

    def test_empty_historical_pandascore_cache_is_refetched(self):
        provider = PandaScoreCS2Provider(token="test")
        with patch("app.providers.pandascore.provider_payload_state", return_value=None), patch.object(provider, "_fetch_bucket_page", return_value=[{"id": 1}]) as fetch:
            items = provider._fetch_bucket("past", start=datetime(2020,1,1,tzinfo=timezone.utc), end=datetime(2020,2,1,tzinfo=timezone.utc))
        self.assertEqual(items, [{"id": 1}])
        fetch.assert_called_once()

    def test_pandascore_uses_scheduled_time_when_begin_is_null(self):
        item = {"id": 1, "begin_at": None, "scheduled_at": "2026-09-06T14:00:00Z", "status": "not_started", "opponents": [{"opponent": {"id":3216,"name":"Natus Vincere"}}], "league": None, "serie": None}
        event = PandaScoreCS2Provider(token="test")._match_to_event(item)
        self.assertEqual(event.starts_at, datetime(2026,9,6,14,tzinfo=timezone.utc))
        self.assertIn("navi_cs2", event.entity_ids)

    def test_espn_requests_complete_month_with_explicit_limit(self):
        provider = EspnFootballProvider()
        with patch("app.providers.espn_football.urlopen", return_value=BytesIO(json.dumps({"events":[]}).encode())) as fetch:
            provider._scoreboard("esp.1", "202602")
        query = parse_qs(urlparse(fetch.call_args.args[0].full_url).query)
        self.assertEqual(query["dates"], ["20260201-20260228"])
        self.assertEqual(query["limit"], ["1000"])

    def test_completed_refresh_callback_does_not_deadlock(self):
        future = Future()
        future.set_result([])
        with patch.object(registry._PROVIDER_EXECUTOR, "submit", return_value=future):
            thread = Thread(target=registry.refresh_provider_async, args=("instant", PandaScoreCS2Provider(token="test"), "PandaScoreCS2Provider", "instant", None, None), daemon=True)
            thread.start()
            thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertNotIn("instant", registry._IN_FLIGHT_REFRESHES)

    def test_partial_calendar_returns_refresh_header_even_with_existing_events(self):
        from app.main import calendar_month
        from fastapi import Response
        event = Event(id="real", title="Valencia vs Barcelona", sport=Sport.FOOTBALL, starts_at=datetime(2026,9,6,tzinfo=timezone.utc), status=EventStatus.UPCOMING, entity_ids=["barcelona"], source="football-data")
        response = Response()
        with patch("app.main.preferences_for_user", return_value=DEFAULT_PREFERENCES), patch("app.main.fetch_events", return_value=[event]), patch("app.main.refresh_is_running", return_value=True):
            groups = calendar_month(response, 2026, 9, current_user=None)
        self.assertEqual(response.headers["X-MatchNest-Refreshing"], "true")
        self.assertEqual(len(groups), 1)

    def test_cleanup_archives_only_the_exact_leaked_test_fixture(self):
        from app.storage import connect, archive_leaked_test_fixture, get_event, get_cached_provider_payload, upsert_events
        fake = Event(id="football-1", title="FC Barcelona vs Real Madrid CF", sport=Sport.FOOTBALL, starts_at=datetime(2026,9,25,19,tzinfo=timezone.utc), status=EventStatus.UPCOMING, entity_ids=["barcelona"], source="football-data")
        upsert_events([fake])
        connection = connect()
        self.assertTrue(archive_leaked_test_fixture(connection))
        connection.commit()
        self.assertIsNone(get_event(fake.id))
        self.assertEqual(get_cached_provider_payload("matchnest-repair", "leaked-test-fixture:football-1")["title"], fake.title)
        self.assertFalse(archive_leaked_test_fixture(connection))
        fake.title = "Different real event"
        upsert_events([fake])
        self.assertFalse(archive_leaked_test_fixture(connection))
        self.assertIsNotNone(get_event(fake.id))
        connection.close()

    def test_incomplete_month_snapshot_is_not_frozen_as_history(self):
        from app.storage import ProviderPayloadState
        provider = PandaScoreCS2Provider(token="test")
        start = datetime(2020,8,1,tzinfo=timezone.utc)
        end = datetime(2020,9,1,tzinfo=timezone.utc)
        stale = ProviderPayloadState([{"id": 1}], datetime(2020,8,12,tzinfo=timezone.utc))
        with patch("app.providers.pandascore.provider_payload_state", return_value=stale), patch.object(provider, "_fetch_bucket_page", return_value=[{"id": 1}, {"id": 2}]) as fetch:
            items = provider._fetch_bucket("past", start=start, end=end)
        self.assertEqual(len(items), 2)
        fetch.assert_called_once()
