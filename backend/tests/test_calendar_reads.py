from unittest.mock import patch
from datetime import datetime, timezone
from isolated_case import IsolatedTestCase
from app import storage
from app.providers import registry
from app.providers.espn_football import EspnFootballProvider
from app.models import Event, EventStatus, Sport


class CalendarReadTests(IsolatedTestCase):
    def test_provider_states_are_read_in_one_connection_and_match_individual_reads(self):
        storage.mark_provider_fetch("a","august","ok")
        storage.mark_provider_fetch("b","august","error","limited")
        with patch.object(storage,"connect",wraps=storage.connect) as connect:
            states=storage.provider_fetch_states([("a","august"),("b","august"),("a","october")])
            self.assertEqual(connect.call_count,1)
        self.assertEqual(states[("a","august")],storage.provider_fetch_state("a","august"))
        self.assertEqual(states[("b","august")].error,"limited")
        self.assertNotIn(("a","october"),states)

    def test_existing_account_preferences_do_not_reinitialize_or_write_defaults(self):
        user,_=storage.create_user("calendar-test@example.com","test-password-long")
        with patch.object(storage,"initialize_user_preferences",side_effect=AssertionError("unexpected write")):
            preferences=storage.preferences_for_user(user.id)
        self.assertTrue(preferences.follows)

    def test_cached_month_uses_one_events_read_and_one_refresh_metadata_read(self):
        registry._CACHE.clear()
        self.addCleanup(registry._CACHE.clear)
        start=datetime(2026,8,1,tzinfo=timezone.utc)
        end=datetime(2026,9,1,tzinfo=timezone.utc)
        event=Event("real-august","Barcelona match",Sport.FOOTBALL,start,EventStatus.PAST,["barcelona"],"espn")
        provider=EspnFootballProvider(team_ids={"barcelona":{"83"}})
        key=registry.provider_schedule_key(provider,start,end)
        state=storage.ProviderFetchState(datetime.now(timezone.utc),"ok",None)
        with patch.object(registry,"configured_providers",return_value=[provider]), patch.object(registry,"list_events",return_value=[event]) as events, patch.object(registry,"provider_fetch_states",return_value={(type(provider).__name__,key):state}) as metadata:
            _,result=registry.provider_results(start,end)
            self.assertEqual(result,[event])
            self.assertEqual(events.call_count,1)
            self.assertEqual(metadata.call_count,1)
