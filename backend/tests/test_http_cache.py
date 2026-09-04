from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request
import json
import os

from isolated_case import IsolatedTestCase
from app.providers import http_cache as cache
from app.providers.api_football import ApiFootballProvider
from app.providers.football_data import FootballDataProvider
from app.providers.pandascore import schedule_buckets
from app.providers.registry import provider_refresh_ttl


class HttpCacheTests(IsolatedTestCase):
    def test_concurrent_reads_and_restart_use_one_upstream_request(self):
        request = Request("https://api.pandascore.co/csgo/teams", headers={"Authorization": "Bearer test"})
        def read():
            with cache.cached_urlopen(request) as response:
                return json.loads(response.read())
        with patch.object(cache, "upstream_urlopen", side_effect=lambda *a, **kw: BytesIO(b'[{"id":1}]')) as network:
            with ThreadPoolExecutor(max_workers=6) as workers:
                self.assertEqual(list(workers.map(lambda _: read(), range(12))), [[{"id":1}]] * 12)
            cache.clear_http_memory()
            self.assertEqual(read(), [{"id":1}])
            self.assertEqual(network.call_count, 1)

    def test_rotating_credentials_does_not_reuse_another_tokens_response(self):
        with patch.object(cache, "upstream_urlopen", side_effect=lambda *a, **kw: BytesIO(b'[]')) as network:
            for token in ["first", "second", "first"]:
                cache.cached_urlopen(Request("https://api.pandascore.co/csgo/teams", headers={"Authorization":token})).close()
            self.assertEqual(network.call_count, 2)

    def test_429_cooldown_is_shared_across_urls_and_survives_restart(self):
        error=HTTPError("https://api.pandascore.co/",429,"limited",{"Retry-After":"3600"},BytesIO())
        with patch.object(cache, "upstream_urlopen", side_effect=error) as network:
            for index in range(3):
                cache.clear_http_memory()
                with self.assertRaises(HTTPError) as raised:
                    cache.cached_urlopen(f"https://api.pandascore.co/csgo/matches/upcoming?page={index}")
                self.assertEqual(raised.exception.code,429)
            self.assertEqual(network.call_count,1)

    def test_fresh_cache_is_available_during_provider_cooldown(self):
        with patch.object(cache, "upstream_urlopen", side_effect=[BytesIO(b'[]'),HTTPError("url",429,"limited",{},BytesIO())]) as network:
            cache.cached_urlopen("https://api.pandascore.co/csgo/teams").close()
            with self.assertRaises(HTTPError):
                cache.cached_urlopen("https://api.pandascore.co/csgo/matches/running")
            with cache.cached_urlopen("https://api.pandascore.co/csgo/teams") as response:
                self.assertEqual(response.read(),b'[]')
            self.assertEqual(network.call_count,2)

    def test_resource_403_does_not_block_other_football_feeds(self):
        with patch.object(cache, "upstream_urlopen", side_effect=[HTTPError("url",403,"restricted",{},BytesIO()),BytesIO(b'{"matches":[]}')]) as network:
            for _ in range(2):
                with self.assertRaises(HTTPError):
                    cache.cached_urlopen("https://api.football-data.org/v4/teams/794/matches")
            cache.cached_urlopen("https://api.football-data.org/v4/teams/81/matches").close()
            self.assertEqual(network.call_count,2)

    def test_http_200_api_errors_are_not_cached_as_success(self):
        with patch.object(cache, "upstream_urlopen", side_effect=lambda *a,**kw: BytesIO(b'{"errors":{"access":"suspended"}}')) as network:
            for index in range(2):
                with self.assertRaises(HTTPError):
                    cache.cached_urlopen(f"https://v3.football.api-sports.io/fixtures?team={index}")
            self.assertEqual(network.call_count,1)

    def test_local_football_budget_limits_distinct_requests_and_survives_restart(self):
        with patch.object(cache, "upstream_urlopen", side_effect=lambda *a,**kw: BytesIO(b'{}')) as network:
            for index in range(8):
                cache.cached_urlopen(f"https://api.football-data.org/v4/matches?page={index}").close()
            cache.clear_http_memory()
            with self.assertRaises(HTTPError) as raised:
                cache.cached_urlopen("https://api.football-data.org/v4/matches?page=9")
            self.assertEqual(raised.exception.code,429)
            self.assertEqual(network.call_count,8)

    def test_retry_after_accepts_seconds_and_http_date(self):
        now=datetime.now(timezone.utc).replace(microsecond=0)
        self.assertEqual(cache.retry_after_seconds("180",now),180)
        self.assertEqual(cache.retry_after_seconds(format_datetime(now+timedelta(minutes=5)),now),300)
        self.assertEqual(cache.retry_after_seconds("invalid",now),90)

    def test_failure_expires_and_success_can_be_retried(self):
        with patch.object(cache,"upstream_urlopen",side_effect=[OSError("offline"),BytesIO(b'{}')]) as network:
            with self.assertRaises(OSError): cache.cached_urlopen("https://example.com/data")
            with self.assertRaises(HTTPError): cache.cached_urlopen("https://example.com/data")
            future=datetime.now(timezone.utc)+timedelta(minutes=3)
            with patch.object(cache,"datetime") as clock:
                clock.now.return_value=future
                cache.cached_urlopen("https://example.com/data").close()
            self.assertEqual(network.call_count,2)

    def test_historical_schedule_cache_lasts_longer_than_live(self):
        now=datetime.now(timezone.utc)
        self.assertEqual(schedule_buckets(now-timedelta(days=35),now-timedelta(days=5)),["past"])
        self.assertEqual(schedule_buckets(now+timedelta(days=3),now+timedelta(days=35)),["upcoming"])
        self.assertGreater(provider_refresh_ttl("PandaScoreCS2Provider",now-timedelta(days=35),now-timedelta(days=5)),provider_refresh_ttl("PandaScoreCS2Provider",now,now+timedelta(days=1)))

    def test_api_football_requires_enable_flag_for_environment_token(self):
        with patch.dict(os.environ,{"API_FOOTBALL_TOKEN":"test","API_FOOTBALL_ENABLE":"0"}):
            self.assertIsNone(ApiFootballProvider().token)
            self.assertEqual(ApiFootballProvider(token="explicit-test").token,"explicit-test")
        with patch.dict(os.environ,{"API_FOOTBALL_TOKEN":"test","API_FOOTBALL_ENABLE":"1"}):
            self.assertEqual(ApiFootballProvider().token,"test")

    def test_football_empty_follow_selection_does_not_restore_default_teams(self):
        provider=FootballDataProvider(token="test",team_queries={},competition_entities={})
        with patch.object(provider,"_fetch_team_matches") as network:
            self.assertEqual(provider.fetch(),[])
            network.assert_not_called()

    def test_football_aggregate_cache_is_scoped_by_follow_selection(self):
        first=FootballDataProvider(token="test",team_queries={},team_entities={"a":1},competition_entities={})
        second=FootballDataProvider(token="test",team_queries={},team_entities={"b":2},competition_entities={})
        with patch.object(first,"_fetch_followed_matches",return_value=[]) as one, patch.object(second,"_fetch_followed_matches",return_value=[]) as two:
            first.fetch()
            second.fetch()
            first.fetch()
            self.assertEqual(one.call_count,1)
            self.assertEqual(two.call_count,1)

    def test_html_response_preserves_url_for_rankings_parser(self):
        with patch.object(cache,"upstream_urlopen",return_value=BytesIO(b'<html>ranking</html>')):
            with cache.cached_urlopen("https://www.hltv.org/ranking/teams") as response:
                self.assertEqual(response.geturl(),"https://www.hltv.org/ranking/teams")
                self.assertEqual(response.read(),b'<html>ranking</html>')

    def test_provider_schedule_keys_share_cs2_but_separate_football_selections(self):
        from app.providers.pandascore import PandaScoreCS2Provider
        from app.providers.registry import provider_schedule_key
        self.assertEqual(provider_schedule_key(PandaScoreCS2Provider(token="test"),None,None),provider_schedule_key(PandaScoreCS2Provider(token="test"),None,None))
        a=FootballDataProvider(token="test",team_queries={},team_entities={"a":1})
        b=FootballDataProvider(token="test",team_queries={},team_entities={"b":2})
        self.assertNotEqual(provider_schedule_key(a,None,None),provider_schedule_key(b,None,None))

    def test_queued_refresh_rechecks_the_shared_schedule_cache(self):
        from app.providers import registry
        with patch.object(registry,"provider_should_refresh",return_value=False), patch.object(registry,"refresh_provider") as refresh:
            self.assertEqual(registry.refresh_provider_if_needed(None,"test","shared",None,None),[])
            refresh.assert_not_called()

    def test_rolling_cron_windows_use_stable_keys_and_expand_bounds(self):
        from app.providers.registry import provider_schedule_key, provider_date_range
        from app.providers.pandascore import PandaScoreCS2Provider
        start=datetime(2026,9,4,7,12,tzinfo=timezone.utc)
        end=start+timedelta(days=1)
        provider=PandaScoreCS2Provider(token="test")
        self.assertEqual(provider_schedule_key(provider,start,end),provider_schedule_key(provider,start+timedelta(minutes=1),end+timedelta(minutes=1)))
        expanded_start,expanded_end=provider_date_range(start,end)
        self.assertLessEqual(expanded_start,start)
        self.assertGreaterEqual(expanded_end,end)
        self.assertEqual(expanded_start.hour,0)
        self.assertEqual(expanded_end.hour,0)

    def test_remaining_zero_header_pauses_other_urls_but_serves_cached_data(self):
        response=BytesIO(b'[]')
        response.headers={"X-Rate-Limit-Remaining":"0"}
        with patch.object(cache,"upstream_urlopen",return_value=response) as network:
            cache.cached_urlopen("https://api.pandascore.co/csgo/teams").close()
            with self.assertRaises(HTTPError): cache.cached_urlopen("https://api.pandascore.co/csgo/matches/running")
            cache.cached_urlopen("https://api.pandascore.co/csgo/teams").close()
            self.assertEqual(network.call_count,1)
