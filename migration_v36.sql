-- How an employee prefers to work ("2/2"), stored with weekday wishes. Apply after v35.
-- A wish may now be only a work pattern, only weekdays, or both.
BEGIN;
ALTER TABLE weekday_wishes ADD COLUMN pattern TEXT CHECK(pattern ~ '^[1-7]/[1-7]$');
ALTER TABLE weekday_wishes DROP CONSTRAINT weekday_wishes_days_check;
ALTER TABLE weekday_wishes ADD CONSTRAINT weekday_wishes_days_check
 CHECK(jsonb_typeof(days)='array' AND jsonb_array_length(days) BETWEEN 0 AND 7);
ALTER TABLE weekday_wishes ADD CONSTRAINT weekday_wishes_not_empty
 CHECK(jsonb_array_length(days)>0 OR pattern IS NOT NULL);
NOTIFY pgrst, 'reload schema';
COMMIT;
