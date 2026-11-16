-- Brings either version of v46 to the final trainings schema. Apply after v46.
-- The first v46 had training_posts (one team-chat message per session); the bot now posts one short announcement
-- and invites people personally, so sessions, invitations and sign-ups are tied together. Safe to run on the final v46.
BEGIN;
CREATE TABLE IF NOT EXISTS training_sessions (
  restaurant_id uuid NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
  session_key text NOT NULL CHECK (char_length(session_key) BETWEEN 1 AND 80),
  short_id text NOT NULL CHECK (short_id ~ '^[0-9a-f]{10}$'),
  session jsonb NOT NULL CHECK (jsonb_typeof(session) = 'object' AND octet_length(session::text) <= 2000),
  announced_at timestamptz,
  reported_at timestamptz,
  PRIMARY KEY (restaurant_id, session_key),
  UNIQUE (restaurant_id, short_id)
);
CREATE TABLE IF NOT EXISTS training_invites (
  restaurant_id uuid NOT NULL,
  session_key text NOT NULL,
  link_id uuid NOT NULL REFERENCES employee_links(id) ON DELETE CASCADE,
  sent_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (restaurant_id, session_key, link_id),
  FOREIGN KEY (restaurant_id, session_key) REFERENCES training_sessions(restaurant_id, session_key) ON DELETE CASCADE
);
-- The first v46 had no link between a sign-up and its session. The old code never ran, so there are no rows;
-- if there were, the constraint would fail and the whole migration would roll back, losing nothing.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'training_signups'::regclass AND contype = 'f'
                 AND confrelid = 'training_sessions'::regclass) THEN
    ALTER TABLE training_signups ADD CONSTRAINT training_signups_session
      FOREIGN KEY (restaurant_id, session_key) REFERENCES training_sessions(restaurant_id, session_key) ON DELETE CASCADE;
  END IF;
END $$;
-- Only the first v46 had it, and nothing ever wrote to it.
DROP TABLE IF EXISTS training_posts;
ALTER TABLE training_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE training_invites ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON training_sessions, training_invites FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON training_sessions, training_invites TO service_role;
NOTIFY pgrst, 'reload schema';
COMMIT;
