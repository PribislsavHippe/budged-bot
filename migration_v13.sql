-- Apply after v12, before deploying evening shift reminders.
BEGIN;
ALTER TABLE users ADD COLUMN shift_reminders_enabled BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE shifts ADD COLUMN start_reminder_sent BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE research_subjects ALTER COLUMN onboarding_version SET DEFAULT 2;

-- A corrected start time should produce one reminder for the corrected shift.
CREATE FUNCTION reset_shift_start_reminder() RETURNS trigger
LANGUAGE plpgsql SET search_path=public AS $$
BEGIN
 IF OLD.starts_at IS DISTINCT FROM NEW.starts_at OR OLD.ends_at IS DISTINCT FROM NEW.ends_at THEN
  NEW.start_reminder_sent := false;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER reset_shift_start_reminder_before_update
BEFORE UPDATE ON shifts FOR EACH ROW EXECUTE FUNCTION reset_shift_start_reminder();
NOTIFY pgrst, 'reload schema';
COMMIT;
