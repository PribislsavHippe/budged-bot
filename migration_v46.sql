-- Trainings: the team chat and the sheet of a restaurant, sessions read from the sheet, personal invitations,
-- sign-ups made by the bot. Apply after v45.
BEGIN;
CREATE TABLE training_chats (
  restaurant_id uuid PRIMARY KEY REFERENCES restaurants(id) ON DELETE CASCADE,
  chat_id bigint NOT NULL UNIQUE CHECK (chat_id < 0),
  -- One file for all months: the bot reads every tab of it.
  sheet_id text CHECK (sheet_id ~ '^[A-Za-z0-9_-]{20,100}$'),
  -- How the restaurant and the position are written in the sheet ("Итали", "официант").
  label text CHECK (char_length(label) BETWEEN 1 AND 40),
  position text NOT NULL DEFAULT 'официант' CHECK (char_length(position) BETWEEN 1 AND 40),
  active boolean NOT NULL DEFAULT true,
  bound_by bigint REFERENCES users(id) ON DELETE SET NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE training_sessions (
  restaurant_id uuid NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
  session_key text NOT NULL CHECK (char_length(session_key) BETWEEN 1 AND 80),
  -- Short id for buttons (Telegram allows 64 bytes of button data).
  short_id text NOT NULL CHECK (short_id ~ '^[0-9a-f]{10}$'),
  -- Date, time, title, host, tab and column as last read from the sheet; never names of other people.
  session jsonb NOT NULL CHECK (jsonb_typeof(session) = 'object' AND octet_length(session::text) <= 2000),
  announced_at timestamptz,
  reported_at timestamptz,
  PRIMARY KEY (restaurant_id, session_key),
  UNIQUE (restaurant_id, short_id)
);
CREATE TABLE training_invites (
  restaurant_id uuid NOT NULL,
  session_key text NOT NULL,
  link_id uuid NOT NULL REFERENCES employee_links(id) ON DELETE CASCADE,
  sent_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (restaurant_id, session_key, link_id),
  FOREIGN KEY (restaurant_id, session_key) REFERENCES training_sessions(restaurant_id, session_key) ON DELETE CASCADE
);
CREATE TABLE training_signups (
  restaurant_id uuid NOT NULL,
  session_key text NOT NULL,
  link_id uuid NOT NULL REFERENCES employee_links(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (restaurant_id, session_key, link_id),
  FOREIGN KEY (restaurant_id, session_key) REFERENCES training_sessions(restaurant_id, session_key) ON DELETE CASCADE
);
ALTER TABLE training_chats ENABLE ROW LEVEL SECURITY;
ALTER TABLE training_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE training_invites ENABLE ROW LEVEL SECURITY;
ALTER TABLE training_signups ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON training_chats, training_sessions, training_invites, training_signups FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON training_chats, training_sessions, training_invites, training_signups TO service_role;
NOTIFY pgrst, 'reload schema';
COMMIT;
