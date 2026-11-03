-- Restaurant shift schedules built on the website. Additive, apply after v29.
-- The owner can let an approved employee (the schedule manager) build schedules;
-- that right opens only the schedule, never sales or personal records.
BEGIN;
ALTER TABLE employee_links ADD COLUMN can_schedule BOOLEAN NOT NULL DEFAULT false;

CREATE TABLE rosters (
 id UUID PRIMARY KEY,
 restaurant_id UUID NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
 start_date DATE NOT NULL,
 end_date DATE NOT NULL CHECK(end_date>=start_date AND end_date-start_date<62),
 revision INTEGER NOT NULL DEFAULT 1,
 -- Draft being edited: people, days off, wishes, per-day settings, events and cells.
 draft JSONB NOT NULL CHECK(jsonb_typeof(draft)='object'),
 -- Snapshot the team sees; NULL until the first publication.
 published JSONB CHECK(published IS NULL OR jsonb_typeof(published)='object'),
 published_at TIMESTAMPTZ,
 updated_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(restaurant_id,start_date)
);
CREATE INDEX rosters_restaurant ON rosters(restaurant_id,start_date);
ALTER TABLE rosters ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON rosters FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON rosters TO service_role;
COMMIT;
