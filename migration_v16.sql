-- Distinguish planned shifts from worked hours; complete onboarding via schedule.
BEGIN;
ALTER TABLE analytics_events DROP CONSTRAINT analytics_events_event_check;
ALTER TABLE analytics_events ADD CONSTRAINT analytics_events_event_check CHECK(event IN (
 'user_started','activity','onboarding_started','onboarding_step','onboarding_skipped','onboarding_completed',
 'tip_added','expense_added','first_tip_added','first_expense_added','first_value_action',
 'cabinet_opened','cabinet_loaded','cabinet_load_error','tab_opened',
 'shift_closed','first_shift_closed','shift_planned','hours_recorded',
 'sales_report_started','sales_report_completed','sales_report_error',
 'vision_started','vision_completed','vision_failed','help_opened','problem_reported'));

CREATE OR REPLACE FUNCTION record_ux_event(actor BIGINT, kind TEXT, origin TEXT, screen_name TEXT DEFAULT NULL,
 step_name TEXT DEFAULT NULL, failure_code TEXT DEFAULT NULL, release TEXT DEFAULT 'ux-1', operation TEXT DEFAULT NULL,
 event_time TIMESTAMPTZ DEFAULT clock_timestamp())
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE s research_subjects; k TEXT; inserted_id BIGINT; first_action BOOLEAN:=FALSE;
BEGIN
 SELECT * INTO s FROM research_subjects WHERE user_id=actor FOR UPDATE;
 IF NOT FOUND THEN RETURN '{}'::jsonb; END IF;
 IF kind='onboarding_started' THEN
   IF s.onboarding_state NOT IN ('not_started') THEN RETURN jsonb_build_object('state',s.onboarding_state); END IF;
   UPDATE research_subjects SET onboarding_state='started' WHERE id=s.id;
 ELSIF kind='onboarding_skipped' THEN
   IF s.onboarding_state NOT IN ('not_started','started') THEN RETURN jsonb_build_object('state',s.onboarding_state); END IF;
   UPDATE research_subjects SET onboarding_state='skipped' WHERE id=s.id;
 END IF;
 INSERT INTO analytics_events(subject_id,event,onboarding_version,app_version,source,screen,step,error_code,dedupe_key,occurred_at)
 VALUES(s.id,kind,s.onboarding_version,release,origin,screen_name,step_name,failure_code,
   CASE WHEN kind IN ('user_started','onboarding_started','onboarding_skipped') THEN 'once' ELSE operation END,event_time)
 ON CONFLICT DO NOTHING RETURNING id INTO inserted_id;
 IF inserted_id IS NULL THEN RETURN jsonb_build_object('state',s.onboarding_state); END IF;
 IF kind IN ('tip_added','expense_added') THEN
   k:=CASE WHEN kind='tip_added' THEN 'first_tip_added' ELSE 'first_expense_added' END;
   INSERT INTO analytics_events(subject_id,event,onboarding_version,app_version,source,screen,dedupe_key,occurred_at)
     VALUES(s.id,k,s.onboarding_version,release,origin,screen_name,'once',event_time) ON CONFLICT DO NOTHING;
 END IF;
 IF kind IN ('tip_added','expense_added','sales_report_completed','shift_planned') THEN
   INSERT INTO analytics_events(subject_id,event,onboarding_version,app_version,source,screen,dedupe_key,occurred_at)
     VALUES(s.id,'first_value_action',s.onboarding_version,release,origin,screen_name,'once',event_time)
     ON CONFLICT DO NOTHING RETURNING id INTO inserted_id;
   first_action:=inserted_id IS NOT NULL;
   IF s.onboarding_state='started' THEN
     UPDATE research_subjects SET onboarding_state='completed' WHERE id=s.id;
     INSERT INTO analytics_events(subject_id,event,onboarding_version,app_version,source,screen,dedupe_key,occurred_at)
       VALUES(s.id,'onboarding_completed',s.onboarding_version,release,origin,screen_name,'once',event_time) ON CONFLICT DO NOTHING;
   END IF;
 END IF;
 IF kind='shift_closed' THEN
   INSERT INTO analytics_events(subject_id,event,onboarding_version,app_version,source,screen,dedupe_key,occurred_at)
     VALUES(s.id,'first_shift_closed',s.onboarding_version,release,origin,screen_name,'once',event_time) ON CONFLICT DO NOTHING;
 END IF;
 RETURN jsonb_build_object('first_value',first_action,'was_learning',s.onboarding_state='started');
END $$;
NOTIFY pgrst, 'reload schema';
COMMIT;
