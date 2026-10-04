-- Explicit work dates and idempotent chat entries. Apply after v13.
BEGIN;

ALTER TABLE entries ADD COLUMN IF NOT EXISTS work_date DATE;
UPDATE entries
SET work_date = (created_at AT TIME ZONE 'Europe/Moscow' - interval '6 hours')::date
WHERE work_date IS NULL;
ALTER TABLE entries ALTER COLUMN work_date SET DEFAULT ((now() AT TIME ZONE 'Europe/Moscow' - interval '6 hours')::date);
ALTER TABLE entries ALTER COLUMN work_date SET NOT NULL;
CREATE INDEX IF NOT EXISTS entries_user_work_date ON entries(user_id, work_date);

ALTER TABLE entries ADD COLUMN IF NOT EXISTS source_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS entries_user_source_key
  ON entries(user_id, source_key) WHERE source_key IS NOT NULL;

ALTER TABLE shifts DROP CONSTRAINT IF EXISTS shift_time_pair;
ALTER TABLE shifts ADD CONSTRAINT shift_time_pair CHECK(
  (starts_at IS NULL AND ends_at IS NULL) OR
  (starts_at IS NOT NULL AND ends_at IS NOT NULL AND ends_at<>starts_at));

-- Calendar edits use a plain upsert, so the end reminder must reset on a
-- changed shift just as it does for the schedule import RPC.
CREATE FUNCTION reset_shift_end_reminder() RETURNS trigger
LANGUAGE plpgsql SET search_path=public AS $$
BEGIN
 IF OLD.starts_at IS DISTINCT FROM NEW.starts_at OR OLD.ends_at IS DISTINCT FROM NEW.ends_at THEN
  NEW.time_prompt_sent := false;
  NEW.time_prompt_at := NULL;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER reset_shift_end_reminder_before_update
BEFORE UPDATE ON shifts FOR EACH ROW EXECUTE FUNCTION reset_shift_end_reminder();

NOTIFY pgrst, 'reload schema';
COMMIT;
