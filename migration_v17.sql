-- Private, revocable iPhone calendar subscriptions. Apply after v16.
BEGIN;

CREATE TABLE calendar_subscriptions (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    feed_id UUID NOT NULL UNIQUE,
    secret_salt TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    rotated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE calendar_subscriptions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON calendar_subscriptions FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON calendar_subscriptions TO service_role;

-- Keep a stable modification timestamp for subscribed calendar events.
ALTER TABLE shifts ADD COLUMN calendar_updated_at TIMESTAMPTZ;
UPDATE shifts SET calendar_updated_at = COALESCE(created_at, now())
WHERE calendar_updated_at IS NULL;
ALTER TABLE shifts ALTER COLUMN calendar_updated_at SET DEFAULT now();
ALTER TABLE shifts ALTER COLUMN calendar_updated_at SET NOT NULL;

CREATE FUNCTION touch_shift_calendar() RETURNS trigger
LANGUAGE plpgsql SET search_path=public AS $$
BEGIN
  IF OLD.shift_date IS DISTINCT FROM NEW.shift_date
     OR OLD.starts_at IS DISTINCT FROM NEW.starts_at
     OR OLD.ends_at IS DISTINCT FROM NEW.ends_at THEN
    NEW.calendar_updated_at = clock_timestamp();
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER touch_shift_calendar_before_update
BEFORE UPDATE ON shifts FOR EACH ROW EXECUTE FUNCTION touch_shift_calendar();

NOTIFY pgrst, 'reload schema';
COMMIT;
