-- Content-free steps for studying repeated schedule imports.
BEGIN;
ALTER TABLE analytics_events DROP CONSTRAINT analytics_events_event_check;
ALTER TABLE analytics_events ADD CONSTRAINT analytics_events_event_check CHECK(event IN (
 'user_started','activity','onboarding_started','onboarding_step','onboarding_skipped','onboarding_completed',
 'tip_added','expense_added','first_tip_added','first_expense_added','first_value_action',
 'cabinet_opened','cabinet_loaded','cabinet_load_error','tab_opened',
 'shift_closed','first_shift_closed','shift_planned','hours_recorded',
 'sales_report_started','sales_report_completed','sales_report_error',
 'vision_started','vision_completed','vision_failed','help_opened','problem_reported',
 'schedule_import_started','schedule_previewed','schedule_imported'));
NOTIFY pgrst, 'reload schema';
COMMIT;
