-- Исследование: где люди застревают с ответом о конце смены. Apply after v48.
-- Новые события: shift_end_answer (исход ответа о конце смены) и message_burst (три сообщения за две минуты
-- без успешной записи). Уточнения — только из короткого списка: без текста, сумм и времени смен.
-- «Действие в чате» теперь различает сообщение (message) и нажатие кнопки (button).
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
 'shift_end_answer','message_burst'));
ALTER TABLE analytics_events DROP CONSTRAINT analytics_events_step_check;
ALTER TABLE analytics_events ADD CONSTRAINT analytics_events_step_check CHECK(step IN (
 'tip','expense',
 'shift_tomorrow','shift_end','days_off',
 'no_amount','ambiguous_amount','short_number','unclear','bank_no_amount',
 'undo','account','amount','delete',
 'message','bank',
 'button','time_format','small_amount','understood','no_shift','not_started','timeout','edit','cancel','unknown'));
COMMIT;
