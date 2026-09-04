from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from isolated_case import IsolatedTestCase
from app.models import Event, EventStatus, Sport, UserPreferences
from app.providers.registry import dedupe_cross_source_events, fetch_event_details, football_details_with_stored_result, merged_stored_event
from app.main import spoiler_safe_event_details
from app.storage import upsert_events


class DuplicateDetailsTests(IsolatedTestCase):
    def fixtures(self):
        first=Event("football-espn-esp.1-1","Barcelona vs Rayo Vallecano",Sport.FOOTBALL,datetime(2026,8,31,19,30,tzinfo=timezone.utc),EventStatus.UPCOMING,["barcelona"],"espn",competition="Spanish LALIGA")
        second=replace(first,id="football-2",title="FC Barcelona vs Rayo Vallecano de Madrid",source="football-data",status=EventStatus.PAST,result_summary="FC Barcelona 3-1 Rayo Vallecano de Madrid",entity_ids=["barcelona","league"])
        return first,second

    def test_screenshot_aliases_merge_without_changing_primary_id_or_losing_score(self):
        first,second=self.fixtures()
        merged=dedupe_cross_source_events([second,first])
        self.assertEqual(len(merged),1)
        self.assertEqual(merged[0].id,first.id)
        self.assertEqual(merged[0].status,EventStatus.PAST)
        self.assertEqual(merged[0].result_summary,second.result_summary)
        self.assertIn("league",merged[0].entity_ids)
        self.assertIsNone(first.result_summary)

    def test_distinct_opponents_squads_and_kickoffs_are_not_merged(self):
        first,_=self.fixtures()
        events=[first,replace(first,id="women",title="Barcelona Women vs Rayo Vallecano"),replace(first,id="b",title="Barcelona B vs Rayo Vallecano"),replace(first,id="opponent",title="Barcelona vs Real Madrid"),replace(first,id="later",starts_at=first.starts_at+timedelta(days=1))]
        self.assertEqual(len(dedupe_cross_source_events(events)),5)

    def test_old_football_data_links_have_stored_details_instead_of_404(self):
        _,event=self.fixtures()
        upsert_events([event])
        details=fetch_event_details(event.id)
        self.assertEqual(details["event_id"],event.id)
        self.assertEqual(details["score"]["rows"],[[event.result_summary]])

    def test_stale_scheduled_details_get_known_result_without_placeholder_zeros(self):
        first,second=self.fixtures()
        upsert_events([first,second])
        details={"event_id":first.id,"sport":"football","source":"espn","summary":"Scheduled","facts":[{"label":"Status","value":"Scheduled"}],"sections":[{"title":"Teams","columns":["Team","Score"],"rows":[["Barcelona","0"],["Rayo","0"]]}]}
        result=football_details_with_stored_result(details,first)
        self.assertEqual(result["summary"],second.result_summary)
        self.assertEqual(result["facts"][0]["value"],"past")
        self.assertEqual(result["sections"][1]["columns"],["Team"])

    def test_score_reveal_endpoint_uses_merged_result_with_requested_id(self):
        first,second=self.fixtures()
        upsert_events([first,second])
        result=merged_stored_event(first)
        self.assertEqual(result.id,first.id)
        self.assertEqual(result.result_summary,second.result_summary)

    def test_explicit_details_reveal_bypasses_spoiler_mask_without_changing_preferences(self):
        _,event=self.fixtures()
        preferences=UserPreferences(default_hide_spoilers=True,ui_state={"spoilerMode":"all"})
        details=football_details_with_stored_result(None,event)
        self.assertEqual(spoiler_safe_event_details(details,event,preferences,True)["summary"],event.result_summary)
        self.assertTrue(preferences.default_hide_spoilers)
        safe=spoiler_safe_event_details(details,event,preferences,False)
        self.assertIsNone(safe["score"])

    def test_teams_section_cannot_leak_scores_when_masked(self):
        _,event=self.fixtures()
        preferences=UserPreferences(default_hide_spoilers=True,ui_state={"spoilerMode":"all"})
        details={"summary":"Fixture","sections":[{"title":"Teams","columns":["Team","Score"],"rows":[["Barcelona","3"]]}]}
        safe=spoiler_safe_event_details(details,event,preferences)
        self.assertEqual([section["title"] for section in safe["sections"]],["Spoiler hidden"])

    def test_non_latin_team_names_remain_distinct(self):
        from app.providers.registry import normalize_football_team_name
        self.assertNotEqual(normalize_football_team_name("Динамо"),normalize_football_team_name("Шахтар"))
