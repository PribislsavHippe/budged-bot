-- Content-free steps of the shift schedule for UX research. Apply after v33.
-- Only the fact and the stage are stored: no names, dates, shifts or counts.
BEGIN;
ALTER TABLE analytics_events DROP CONSTRAINT analytics_events_event_check;
ALTER TABLE analytics_events ADD CONSTRAINT analytics_events_event_check CHECK(event IN (
 'user_started','activity','onboarding_started','onboarding_step','onboarding_skipped','onboarding_completed',
 'tip_added','expense_added','first_tip_added','first_expense_added','first_value_action',
 'cabinet_opened','cabinet_loaded','cabinet_load_error','tab_opened',
 'shift_closed','first_shift_closed','shift_planned','hours_recorded',
 'sales_report_started','sales_report_completed','sales_report_error',
 'vision_started','vision_completed','vision_failed','help_opened','problem_reported',
 'schedule_import_started','schedule_previewed','schedule_imported',
 'roster_created','roster_variant_picked','roster_built','roster_dates_changed','roster_published','roster_republished',
 'days_off_asked','days_off_answered','roster_viewed'));
ALTER TABLE analytics_events DROP CONSTRAINT analytics_events_screen_check;
ALTER TABLE analytics_events ADD CONSTRAINT analytics_events_screen_check CHECK(screen IN (
 'chat','earnings','sales','restaurant','research','history','help','calendar','schedule'));
NOTIFY pgrst, 'reload schema';
COMMIT;
