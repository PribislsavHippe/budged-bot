-- Track one personal training reminder per employee and session. Apply after v51.
BEGIN;
CREATE TABLE training_reminders (
  restaurant_id uuid NOT NULL,
  session_key text NOT NULL,
  link_id uuid NOT NULL REFERENCES employee_links(id) ON DELETE CASCADE ON UPDATE CASCADE,
  claimed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (restaurant_id, session_key, link_id),
  FOREIGN KEY (restaurant_id, session_key) REFERENCES training_sessions(restaurant_id, session_key) ON DELETE CASCADE
);
CREATE INDEX training_reminders_link_id_idx ON training_reminders(link_id);
ALTER TABLE training_reminders ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON training_reminders FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON training_reminders TO service_role;
NOTIFY pgrst, 'reload schema';
COMMIT;
