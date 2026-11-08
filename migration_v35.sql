-- Weekday wishes that employees write to the bot ("выходной по вторникам"). Apply after v34.
-- Days are weekday numbers, Monday = 0. The schedule builder treats them as soft wishes.
BEGIN;
CREATE TABLE weekday_wishes (
 link_id UUID PRIMARY KEY REFERENCES employee_links(id) ON DELETE CASCADE,
 restaurant_id UUID NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
 days JSONB NOT NULL CHECK(jsonb_typeof(days)='array' AND jsonb_array_length(days) BETWEEN 1 AND 7),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX weekday_wishes_restaurant ON weekday_wishes(restaurant_id);
ALTER TABLE weekday_wishes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON weekday_wishes FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON weekday_wishes TO service_role;
COMMIT;
