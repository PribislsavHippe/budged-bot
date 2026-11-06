-- Mandatory days off that employees record themselves in Telegram. Apply after v31.
-- The schedule builder takes them as fixed days off; managers see only who answered.
BEGIN;
CREATE TABLE day_off_requests (
 link_id UUID NOT NULL REFERENCES employee_links(id) ON DELETE CASCADE,
 restaurant_id UUID NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
 start_date DATE NOT NULL,
 end_date DATE NOT NULL CHECK(end_date>=start_date AND end_date-start_date<62),
 days JSONB NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(days)='array' AND jsonb_array_length(days)<=12),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(link_id,start_date)
);
CREATE INDEX day_off_requests_period ON day_off_requests(restaurant_id,start_date);
ALTER TABLE day_off_requests ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON day_off_requests FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON day_off_requests TO service_role;
COMMIT;
