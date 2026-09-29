-- Timed schedules and PRIVATE worked hours/pay. After v11, before WORK_TIME_ENABLED=1.
BEGIN;
ALTER TABLE users ADD COLUMN hourly_rate NUMERIC(12,2) CHECK(hourly_rate>0 AND hourly_rate<=1000000);
ALTER TABLE shifts ADD COLUMN starts_at TIME;
ALTER TABLE shifts ADD COLUMN ends_at TIME;
ALTER TABLE shifts ADD COLUMN time_prompt_at TIMESTAMPTZ;
ALTER TABLE shifts ADD COLUMN time_prompt_sent BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE shifts ADD CONSTRAINT shift_time_pair CHECK((starts_at IS NULL AND ends_at IS NULL) OR
 (starts_at IS NOT NULL AND ends_at IS NOT NULL AND ends_at>starts_at));
CREATE TABLE worked_shifts (
 user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 shift_date DATE NOT NULL,
 actual_start TIMESTAMPTZ NOT NULL,
 actual_end TIMESTAMPTZ NOT NULL,
 hourly_rate NUMERIC(12,2) CHECK(hourly_rate>0 AND hourly_rate<=1000000),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(user_id,shift_date),
 CHECK(actual_end>actual_start AND actual_end-actual_start<=interval '24 hours')
);
ALTER TABLE worked_shifts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON worked_shifts FROM anon,authenticated;
GRANT ALL ON worked_shifts TO service_role;
CREATE FUNCTION save_schedule(actor BIGINT, cells JSONB) RETURNS VOID
LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE c JSONB;
BEGIN
 IF jsonb_typeof(cells)<>'array' OR jsonb_array_length(cells) NOT BETWEEN 1 AND 31 THEN RAISE EXCEPTION 'schedule_invalid'; END IF;
 FOR c IN SELECT * FROM jsonb_array_elements(cells) LOOP
  INSERT INTO shifts(user_id,shift_date,starts_at,ends_at) VALUES(actor,(c->>'date')::date,(c->>'start')::time,(c->>'end')::time)
  ON CONFLICT(user_id,shift_date) DO UPDATE SET starts_at=excluded.starts_at,ends_at=excluded.ends_at,
   google_synced=CASE WHEN shifts.starts_at IS DISTINCT FROM excluded.starts_at OR shifts.ends_at IS DISTINCT FROM excluded.ends_at THEN false ELSE shifts.google_synced END,
   time_prompt_sent=CASE WHEN shifts.ends_at IS DISTINCT FROM excluded.ends_at THEN false ELSE shifts.time_prompt_sent END,
   time_prompt_at=CASE WHEN shifts.ends_at IS DISTINCT FROM excluded.ends_at THEN NULL ELSE shifts.time_prompt_at END;
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION save_schedule(BIGINT,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION save_schedule(BIGINT,JSONB) TO service_role;
CREATE FUNCTION set_hourly_rate(actor BIGINT, rate NUMERIC, work_day DATE DEFAULT NULL) RETURNS VOID
LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
BEGIN
 IF rate IS NULL OR rate<=0 OR rate>1000000 OR rate<>round(rate,2) THEN RAISE EXCEPTION 'rate_invalid'; END IF;
 UPDATE users SET hourly_rate=rate WHERE id=actor;
 UPDATE worked_shifts SET hourly_rate=rate WHERE user_id=actor AND shift_date=work_day AND hourly_rate IS NULL;
END $$;
REVOKE ALL ON FUNCTION set_hourly_rate(BIGINT,NUMERIC,DATE) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION set_hourly_rate(BIGINT,NUMERIC,DATE) TO service_role;
NOTIFY pgrst,'reload schema';
COMMIT;
