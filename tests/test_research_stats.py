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
    def test_skip_useful_action_is_counted_independently(self):
        d=summarize(self.subjects(),[event('1','user_started',19),event('1','onboarding_skipped',19),
                                   event('1','first_value_action',19)],now=NOW)
        self.assertEqual(d['first_value_users'],1)
        self.assertEqual(d['funnel'][2]['users'],1)
        self.assertEqual(d['onboarding_skipped'],1)
    def test_planned_shift_is_visible_without_next_day_return(self):
        events=[event('1','user_started',19),event('1','shift_planned',19)]
        d=summarize(self.subjects(),events,now=NOW)
        self.assertEqual(d['retention']['d1']['returned'],0)
        self.assertEqual(d['funnel'][5]['users'],1)
    def test_recent_activity_is_visible_first(self):
        events=[event('1','activity',27),event('2','activity',28),
                event('3','cabinet_load_error',28,error_code='backend'),
                event('4','activity',28,11)]
        d=summarize(self.subjects(),events,now=NOW)
        self.assertEqual([u['id'] for u in d['users']],['4','3','2','1'])
        self.assertEqual(d['active_users'],3)
        self.assertEqual(d['active_today'],2)
        self.assertEqual(d['event_count'],4)
        self.assertEqual(d['last_observed_at'],events[-1]['occurred_at'])
        self.assertEqual(d['daily_activity'][0],{'date':'2026-09-28','active_users':2,'events':3})

    def test_server_confirmed_cabinet_load_counts_as_activity(self):
        d=summarize(self.subjects(),[event('1','cabinet_loaded',28,screen='earnings')],now=NOW)
        self.assertEqual(d['active_today'],1)
        self.assertEqual(d['active_users'],1)
        self.assertEqual(len(d['daily_activity']),30)

    def test_task_metrics_count_people_and_repeat_tips_on_different_days(self):
        events=[event('1','tip_added',19,source='bot'),event('1','tip_added',19,11,source='bot'),
                event('1','tip_added',20,source='miniapp'),event('2','tip_added',28,source='miniapp'),
                event('3','shift_planned',19,screen='calendar'),event('3','shift_planned',20,screen='calendar'),
                event('4','vision_started',19,screen='calendar'),event('4','vision_failed',19,screen='calendar'),
                event('4','vision_started',20,screen='sales')]
        tasks=summarize(self.subjects(),events,now=NOW)['tasks']
        self.assertEqual(tasks['tips']['saved'],2)
        self.assertEqual(tasks['tips']['repeat'],1)
        self.assertEqual(tasks['tips']['repeat_eligible'],1)
        self.assertEqual(tasks['tips']['sources'],{'bot':1,'miniapp':2})
        self.assertEqual(tasks['schedule']['saved'],1)
        self.assertEqual(tasks['schedule']['repeat'],0)
        self.assertEqual(tasks['schedule']['repeat_eligible'],0)
        self.assertEqual(tasks['schedule']['recognition_started'],1)
        self.assertEqual(tasks['schedule']['recognition_failed'],1)

    def test_task_metrics_do_not_call_a_miniapp_open_a_tip_attempt(self):
        events=[event('1','tab_opened',19,screen='earnings'),event('1','cabinet_opened',19,screen='earnings'),
                event('3','cabinet_opened',19,screen='restaurant'),
                event('2','tip_added',20,source='bot'),event('3','vision_completed',21,screen='sales')]
        tasks=summarize(self.subjects(),events,now=NOW)['tasks']
        self.assertEqual(tasks['tips']['miniapp_opened'],1)
        self.assertEqual(tasks['tips']['saved'],1)
        self.assertEqual({row['id'] for row in tasks['tips']['recent']},{'1','2'})
        self.assertEqual(tasks['schedule']['recognition_completed'],0)
        self.assertEqual(tasks['schedule']['saved'],0)

    def test_schedule_repeat_requires_two_confirmed_import_days(self):
        events=[event('1','schedule_import_started',19,screen='calendar'),
                event('1','schedule_previewed',19,screen='calendar'),
                event('1','schedule_imported',19,screen='calendar'),
                event('1','schedule_imported',19,11,screen='calendar'),
                event('1','schedule_imported',20,screen='calendar'),
                event('2','schedule_imported',28,screen='calendar'),
                event('3','shift_planned',19,screen='chat')]
        s=summarize(self.subjects(),events,now=NOW)['tasks']['schedule']
        self.assertEqual(s['import_started'],1)
        self.assertEqual(s['previewed'],1)
        self.assertEqual(s['imported'],2)
        self.assertEqual(s['repeat'],1)
        self.assertEqual(s['repeat_eligible'],1)
        self.assertEqual(s['saved'],1)
