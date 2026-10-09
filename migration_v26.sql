-- Preserve historical onboarding anchors and remove unused OAuth tokens. After v25.
BEGIN;
ALTER TABLE research_subjects ADD COLUMN first_started_at TIMESTAMPTZ;
UPDATE research_subjects s SET first_started_at=history.first_start
 FROM (SELECT subject_id,min(occurred_at) first_start FROM analytics_events
       WHERE event='user_started' GROUP BY subject_id) history WHERE s.id=history.subject_id;
CREATE FUNCTION remember_first_start() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
BEGIN
 IF NEW.event='user_started' THEN
  UPDATE research_subjects SET first_started_at=LEAST(first_started_at,NEW.occurred_at)
  WHERE id=NEW.subject_id;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER analytics_first_start AFTER INSERT ON analytics_events
 FOR EACH ROW EXECUTE FUNCTION remember_first_start();
REVOKE ALL ON FUNCTION remember_first_start() FROM PUBLIC,anon,authenticated;

ALTER TABLE users DROP COLUMN IF EXISTS google_access_token;
ALTER TABLE users DROP COLUMN IF EXISTS google_refresh_token;
ALTER TABLE users DROP COLUMN IF EXISTS google_token_expiry;
ALTER TABLE users DROP COLUMN IF EXISTS google_reconnect_required;

NOTIFY pgrst,'reload schema';
COMMIT;
