-- Исследование: бот предложил помощь после двух непониманий подряд (help_offered). Apply after v49.
-- Без текста и причин: только факт, что человек дошёл до этой точки.
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
 'days_off_asked','days_off_answered','roster_viewed',
 'reminder_sent','message_not_understood','entry_corrected','bot_blocked','calendar_synced',
 'private_mode_enabled','private_mode_disabled','sale_saved','sales_plan_saved',
 'shift_end_answer','message_burst','help_offered'));
COMMIT;
