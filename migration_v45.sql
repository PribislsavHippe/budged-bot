-- Hours chosen in the days-off widget: replace a person's date limits for the rest of the period at once.
BEGIN;
CREATE FUNCTION write_period_hours(p_actor bigint, p_operation uuid, p_roster uuid, p_hours jsonb, p_today date)
RETURNS text LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE
  employee employee_links%ROWTYPE;
  period rosters%ROWTYPE;
  first_day date;
  item record;
  prior jsonb;
BEGIN
  SELECT * INTO period FROM rosters WHERE id = p_roster FOR UPDATE;
  IF NOT FOUND THEN RETURN 'period'; END IF;
  SELECT * INTO employee FROM employee_links
  WHERE user_id = p_actor AND status = 'approved' AND role = 'staff' FOR UPDATE;
  IF NOT FOUND THEN RETURN 'access'; END IF;
  SELECT result INTO prior FROM roster_wish_operations WHERE id = p_operation AND link_id = employee.id;
  IF FOUND THEN RETURN prior->>'status'; END IF;
  IF period.restaurant_id <> employee.restaurant_id OR p_today IS NULL OR period.end_date < p_today
    OR NOT COALESCE(period.draft->'people' @> jsonb_build_array(jsonb_build_object('link', employee.id::text)), false) THEN
    RETURN 'access';
  END IF;
  IF p_hours IS NULL OR jsonb_typeof(p_hours) <> 'object' OR (SELECT count(*) FROM jsonb_object_keys(p_hours)) > 31 THEN
    RETURN 'invalid';
  END IF;
  first_day := greatest(period.start_date, p_today);
  FOR item IN SELECT key, value FROM jsonb_each(p_hours) LOOP
    IF item.key !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' OR jsonb_typeof(item.value) <> 'array'
      OR jsonb_array_length(item.value) <> 2 OR jsonb_typeof(item.value->0) <> 'number'
      OR jsonb_typeof(item.value->1) <> 'number'
      OR (item.value->>0)::integer NOT BETWEEN 360 AND 1379
      OR (item.value->>1)::integer <= (item.value->>0)::integer OR (item.value->>1)::integer > 1380 THEN
      RETURN 'invalid';
    END IF;
    IF item.key::date NOT BETWEEN first_day AND period.end_date THEN RETURN 'period'; END IF;
  END LOOP;
  DELETE FROM shift_time_rules WHERE link_id = employee.id AND restaurant_id = employee.restaurant_id
    AND kind = 'limit' AND target ~ '^[0-9]{4}-' AND target::date BETWEEN first_day AND period.end_date;
  INSERT INTO shift_time_rules (link_id, restaurant_id, target, kind, start_min, end_min, operation_id)
    SELECT employee.id, employee.restaurant_id, key, 'limit', (value->>0)::integer, (value->>1)::integer, p_operation
    FROM jsonb_each(p_hours);
  INSERT INTO roster_wish_operations(id,link_id,result)
    VALUES(p_operation,employee.id,jsonb_build_object('status','ok'));
  RETURN 'ok';
EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow OR invalid_text_representation
  OR numeric_value_out_of_range THEN
  RETURN 'invalid';
END;
$$;
REVOKE ALL ON FUNCTION write_period_hours(bigint,uuid,uuid,jsonb,date) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION write_period_hours(bigint,uuid,uuid,jsonb,date) TO service_role;
NOTIFY pgrst, 'reload schema';
COMMIT;
