import unittest
from datetime import datetime,timezone
from research_stats import summarize

NOW=datetime(2026,9,28,12,tzinfo=timezone.utc)
def event(sid,kind,day,hour=10,**kw):
    return dict(subject_id=sid,event=kind,occurred_at=f'2026-09-{day:02d}T{hour:02d}:00:00+00:00',**kw)

class ResearchMetricsTests(unittest.TestCase):
    def subjects(self):return [dict(id=str(i),label=i,cohort='new',onboarding_version=1) for i in range(1,5)]
    def test_retention_maturity_and_calendar_days(self):
        events=[event('1','user_started',19),event('1','activity',20),event('1','activity',26),
                event('2','user_started',27),event('2','activity',28), # today unfinished D1
                event('3','user_started',20),event('3','activity',21),event('3','activity',27),
                event('4','user_started',26)]
        d=summarize(self.subjects(),events,now=NOW)
        self.assertEqual(d['retention']['d1'],dict(eligible=3,returned=2,pending=1,rate=66.7))
        self.assertEqual(d['retention']['d7'],dict(eligible=2,returned=2,pending=2,rate=100.0))
    def test_error_not_silently_normal_dropoff(self):
        events=[event('1','user_started',19),event('1','onboarding_completed',19),
                event('1','first_value_action',19),event('1','cabinet_opened',19),
                event('1','cabinet_load_error',19,screen='restaurant',error_code='schema')]
        d=summarize(self.subjects(),events,now=NOW)
        self.assertEqual(d['funnel'][4]['drop_off'],1)
        self.assertEqual(d['funnel'][4]['with_errors'],1)
        self.assertEqual(d['errors'][0]['code'],'schema')
    def test_existing_and_versions_not_mixed(self):
        subjects=self.subjects()+[dict(id='old',label=8,cohort='existing',onboarding_version=0)]
        events=[event('old','user_started',19),event('1','user_started',19)]
        d=summarize(subjects,events,now=NOW,ux_version=0)
        self.assertEqual(d['new_users'],0);self.assertIsNone(d['retention']['d1']['rate'])
        self.assertEqual(d['active_users'],1)
    def test_moscow_midnight_and_duplicate_events(self):
        events=[event('1','user_started',19,22),event('1','cabinet_opened',20,22),event('1','activity',20,23)]
        d=summarize(self.subjects(),events,now=NOW)
        self.assertEqual(d['retention']['d1']['returned'],1)
        self.assertEqual(d['new_users'],1)
    def test_skip_useful_action_is_visible_outside_guided_funnel(self):
        d=summarize(self.subjects(),[event('1','user_started',19),event('1','onboarding_skipped',19),
                                   event('1','first_value_action',19)],now=NOW)
        self.assertEqual(d['first_value_users'],1)
        self.assertEqual(d['funnel'][2]['users'],0)
        self.assertEqual(d['onboarding_skipped'],1)
