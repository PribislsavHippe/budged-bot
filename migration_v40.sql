-- Исследование привычки записывать: напоминания, непонятые сообщения, исправления записей,
-- блокировка бота, календарная подписка, приватный режим, продажи. Apply after v39.
-- Хранятся только тип события и уточнение из короткого списка: без сумм, текста, дат и времени смен.
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
 'private_mode_enabled','private_mode_disabled','sale_saved','sales_plan_saved'));
ALTER TABLE analytics_events DROP CONSTRAINT analytics_events_step_check;
ALTER TABLE analytics_events ADD CONSTRAINT analytics_events_step_check CHECK(step IN (
 'tip','expense',
 'shift_tomorrow','shift_end','days_off',
 'no_amount','ambiguous_amount','short_number','unclear','bank_no_amount',
 'undo','account','amount','delete',
 'message','bank'));

-- Роль и смены каждого участника исследования за период. Сопоставление с Telegram ID остаётся внутри
-- базы: наружу выходят только subject_id, роль и даты смен — сервер считает из них количества.
CREATE FUNCTION research_people(since DATE, until DATE)
RETURNS TABLE(subject_id UUID, role TEXT, planned DATE[], worked DATE[])
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=public AS $$
 SELECT s.id,
  CASE WHEN EXISTS(SELECT 1 FROM restaurants r WHERE r.owner_id=s.user_id) THEN 'owner'
   ELSE COALESCE((SELECT CASE WHEN l.status='approved' THEN l.role ELSE 'pending' END
     FROM employee_links l WHERE l.user_id=s.user_id AND l.status IN ('approved','pending')
     ORDER BY l.status='approved' DESC LIMIT 1),'none') END,
  ARRAY(SELECT sh.shift_date FROM shifts sh WHERE sh.user_id=s.user_id
        AND sh.shift_date BETWEEN since AND until ORDER BY 1),
  ARRAY(SELECT w.shift_date FROM worked_shifts w WHERE w.user_id=s.user_id
        AND w.shift_date BETWEEN since AND until ORDER BY 1)
 FROM research_subjects s
$$;
REVOKE ALL ON FUNCTION research_people(DATE,DATE) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION research_people(DATE,DATE) TO service_role;
NOTIFY pgrst, 'reload schema';
COMMIT;
