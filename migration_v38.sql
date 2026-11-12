-- Date-specific and recurring availability rules for staff.
BEGIN;
CREATE TABLE shift_time_rules (
  link_id uuid NOT NULL REFERENCES employee_links(id) ON DELETE CASCADE,
  restaurant_id uuid NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
  target text NOT NULL CHECK (target ~ '^([0-6]|[0-9]{4}-[0-9]{2}-[0-9]{2})$'),
  kind text NOT NULL CHECK (kind IN ('limit','wish')),
  start_min integer NOT NULL CHECK (start_min >= 360 AND start_min < 1380),
  end_min integer CHECK (end_min > start_min AND end_min <= 1380),
  operation_id uuid NOT NULL,
  PRIMARY KEY (link_id, restaurant_id, target, kind)
);
CREATE INDEX shift_time_rules_restaurant ON shift_time_rules(restaurant_id, target);
ALTER TABLE shift_time_rules ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON shift_time_rules FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON shift_time_rules TO service_role;

CREATE FUNCTION write_shift_time_rules(p_actor bigint, p_operation uuid, p_remove uuid,
  p_roster uuid, p_targets text[], p_kind text, p_start integer, p_end integer, p_today date)
RETURNS text LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE
  employee employee_links%ROWTYPE;
  period rosters%ROWTYPE;
  key text;
  prior jsonb;
BEGIN
  IF p_roster IS NOT NULL THEN
    SELECT * INTO period FROM rosters WHERE id = p_roster FOR UPDATE;
    IF NOT FOUND THEN RETURN 'period'; END IF;
  END IF;
  SELECT * INTO employee FROM employee_links
  WHERE user_id = p_actor AND status = 'approved' AND role = 'staff' FOR UPDATE;
  IF NOT FOUND THEN RETURN 'access'; END IF;
  SELECT result INTO prior FROM roster_wish_operations WHERE id = p_operation AND link_id = employee.id;
  IF FOUND THEN RETURN prior->>'status'; END IF;
  IF p_remove IS NOT NULL THEN
    DELETE FROM shift_time_rules WHERE link_id = employee.id AND restaurant_id = employee.restaurant_id
      AND operation_id = p_remove;
    IF NOT FOUND THEN RETURN 'stale'; END IF;
  ELSE
    IF p_roster IS NOT NULL AND (period.restaurant_id <> employee.restaurant_id OR period.end_date < p_today
      OR NOT COALESCE(period.draft->'people' @> jsonb_build_array(jsonb_build_object('link', employee.id::text)), false)) THEN
      RETURN 'access';
    END IF;
    IF COALESCE(cardinality(p_targets), 0) NOT BETWEEN 1 AND 62
      OR p_kind IS NULL OR p_kind NOT IN ('limit','wish')
      OR p_today IS NULL OR p_start IS NULL OR p_start NOT BETWEEN 360 AND 1379
      OR (p_end IS NOT NULL AND (p_end <= p_start OR p_end > 1380)) THEN RETURN 'invalid'; END IF;
    FOREACH key IN ARRAY p_targets LOOP
      IF key ~ '^[0-6]$' THEN CONTINUE; END IF;
      IF key IS NULL OR key !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN RETURN 'invalid'; END IF;
      IF p_roster IS NULL OR key::date NOT BETWEEN greatest(period.start_date, p_today) AND period.end_date
        THEN RETURN 'period'; END IF;
    END LOOP;
    INSERT INTO shift_time_rules (link_id, restaurant_id, target, kind, start_min, end_min, operation_id)
      SELECT employee.id, employee.restaurant_id, t, p_kind, p_start, p_end, p_operation
      FROM (SELECT DISTINCT unnest(p_targets) AS t) AS wanted
    ON CONFLICT (link_id, restaurant_id, target, kind) DO UPDATE
      SET start_min = excluded.start_min, end_min = excluded.end_min, operation_id = excluded.operation_id;
    IF p_roster IS NOT NULL THEN
      INSERT INTO day_off_requests(link_id, restaurant_id, roster_id, start_date, end_date, waiting, answered)
      VALUES(employee.id, employee.restaurant_id, p_roster, period.start_date, period.end_date, false, true)
      ON CONFLICT (link_id, start_date) DO UPDATE SET
        days = CASE WHEN day_off_requests.roster_id = excluded.roster_id THEN day_off_requests.days ELSE '[]' END,
        desired_days = CASE WHEN day_off_requests.roster_id = excluded.roster_id THEN day_off_requests.desired_days ELSE '[]' END,
        roster_id = excluded.roster_id, restaurant_id = excluded.restaurant_id,
        end_date = excluded.end_date, waiting = false, answered = true;
    END IF;
  END IF;
  INSERT INTO roster_wish_operations(id,link_id,result)
    VALUES(p_operation,employee.id,jsonb_build_object('status','ok'));
  RETURN 'ok';
EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN
  RETURN 'invalid';
END;
$$;
REVOKE ALL ON FUNCTION write_shift_time_rules(bigint,uuid,uuid,uuid,text[],text,integer,integer,date)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION write_shift_time_rules(bigint,uuid,uuid,uuid,text[],text,integer,integer,date) TO service_role;
NOTIFY pgrst, 'reload schema';
COMMIT;
