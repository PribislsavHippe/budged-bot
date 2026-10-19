-- Monthly restaurant categories, owner-confirmed; additive, after v26.
BEGIN;
CREATE TABLE category_months (
 restaurant_id UUID NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
 month TEXT NOT NULL CHECK(month ~ '^20[0-9]{2}-(0[1-9]|1[0-2])$'),
 revision INTEGER NOT NULL DEFAULT 1,
 definitions JSONB NOT NULL CHECK(jsonb_typeof(definitions)='array' AND jsonb_array_length(definitions) BETWEEN 1 AND 12),
 updated_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
 operation_id UUID NOT NULL,
 PRIMARY KEY(restaurant_id,month)
);
CREATE TABLE category_reports (
 id UUID PRIMARY KEY,
 user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 restaurant_id UUID REFERENCES restaurants(id) ON DELETE SET NULL,
 month TEXT NOT NULL,
 definitions JSONB NOT NULL,
 metrics JSONB NOT NULL CHECK(jsonb_typeof(metrics)='object'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX category_reports_person ON category_reports(user_id,month,created_at,id);
ALTER TABLE category_months ENABLE ROW LEVEL SECURITY;
ALTER TABLE category_reports ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON category_months,category_reports FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON category_months,category_reports TO service_role;

CREATE FUNCTION configure_categories(actor BIGINT, restaurant UUID, period TEXT, expected INTEGER,
 operation UUID, items JSONB) RETURNS JSONB
LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE previous category_months; item JSONB; names TEXT[]:='{}'; ids TEXT[]:='{}';
BEGIN
 -- Only service_role can invoke. The application ALSO requires signed ADMIN_ID;
 -- restaurant ownership alone never grants access to this application action.
 PERFORM id FROM restaurants WHERE id=restaurant AND owner_id=actor FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'category_forbidden'; END IF;
 SELECT * INTO previous FROM category_months WHERE restaurant_id=restaurant AND month=period FOR UPDATE;
 IF FOUND AND previous.operation_id=operation THEN
   IF previous.definitions<>items THEN RAISE EXCEPTION 'category_operation_conflict'; END IF;
   RETURN to_jsonb(previous);
 END IF;
 IF COALESCE(previous.revision,0)<>expected THEN RAISE EXCEPTION 'category_stale'; END IF;
 IF jsonb_typeof(items)<>'array' OR jsonb_array_length(items) NOT BETWEEN 1 AND 12 THEN
   RAISE EXCEPTION 'category_invalid'; END IF;
 FOR item IN SELECT * FROM jsonb_array_elements(items) LOOP
   IF item->>'unit' NOT IN ('amount','count','number') OR item->>'unit' IS NULL
      OR length(item->>'name') NOT BETWEEN 1 AND 60 OR item->>'name' IS NULL
      OR item->>'id' IS NULL OR item->>'id' !~ '^[0-9a-f-]{36}$'
      OR item->>'id'=ANY(ids) OR lower(item->>'name')=ANY(names)
      OR (item ? 'target' AND (jsonb_typeof(item->'target')<>'number' OR (item->>'target')::numeric<=0
           OR (item->>'target')::numeric>100000000 OR
           (item->>'target')::numeric<>round((item->>'target')::numeric,CASE WHEN item->>'unit'='count' THEN 0 ELSE 2 END)))
      THEN RAISE EXCEPTION 'category_invalid'; END IF;
   PERFORM (item->>'id')::uuid;
   ids:=array_append(ids,item->>'id'); names:=array_append(names,lower(item->>'name'));
 END LOOP;
 INSERT INTO category_months(restaurant_id,month,revision,definitions,updated_by,operation_id)
 VALUES(restaurant,period,expected+1,items,actor,operation)
 ON CONFLICT(restaurant_id,month) DO UPDATE SET revision=excluded.revision,
 definitions=excluded.definitions,updated_by=actor,operation_id=operation;
 RETURN (SELECT to_jsonb(c) FROM category_months c WHERE restaurant_id=restaurant AND month=period);
END $$;

CREATE FUNCTION save_category_report(actor BIGINT, restaurant UUID, period TEXT,
 config_revision INTEGER, operation UUID, metrics JSONB) RETURNS VOID
LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE config category_months; old category_reports; k TEXT; v JSONB; definition JSONB; cutoff DATE;
BEGIN
 -- Lock restaurant before membership/configuration, as identity/transfer do.
 PERFORM id FROM restaurants WHERE id=restaurant FOR SHARE;
 IF NOT EXISTS(SELECT 1 FROM employee_links WHERE user_id=actor AND restaurant_id=restaurant AND status='approved')
    AND NOT EXISTS(SELECT 1 FROM restaurants WHERE id=restaurant AND owner_id=actor)
    THEN RAISE EXCEPTION 'category_forbidden'; END IF;
 SELECT * INTO old FROM category_reports WHERE id=operation;
 IF FOUND THEN
   IF old.user_id<>actor OR old.restaurant_id<>restaurant OR old.month<>period OR old.metrics<>metrics
     THEN RAISE EXCEPTION 'category_operation_conflict'; END IF;
   RETURN;
 END IF;
 SELECT * INTO config FROM category_months WHERE restaurant_id=restaurant AND month=period FOR SHARE;
 IF NOT FOUND OR config.revision<>config_revision THEN RAISE EXCEPTION 'category_stale'; END IF;
 IF jsonb_typeof(metrics)<>'object' OR metrics='{}'::jsonb THEN RAISE EXCEPTION 'category_invalid'; END IF;
 FOR k,v IN SELECT * FROM jsonb_each(metrics) LOOP
   SELECT item INTO definition FROM jsonb_array_elements(config.definitions) item WHERE item->>'id'=k;
   IF definition IS NULL OR jsonb_typeof(v->'value')<>'number' OR NOT(v ? 'cutoff')
      OR jsonb_typeof(v->'cutoff')<>'string' OR v->>'cutoff' IS NULL OR v->>'value' IS NULL OR (v->>'value')::numeric<0 OR (v->>'value')::numeric>100000000
      OR (v->>'value')::numeric<>round((v->>'value')::numeric,CASE WHEN definition->>'unit'='count' THEN 0 ELSE 2 END)
      THEN RAISE EXCEPTION 'category_invalid'; END IF;
   cutoff:=(v->>'cutoff')::date;
   IF to_char(cutoff,'YYYY-MM')<>period OR cutoff>(clock_timestamp() AT TIME ZONE 'Europe/Moscow')::date
      THEN RAISE EXCEPTION 'category_invalid_date'; END IF;
   IF v ? 'target' AND (jsonb_typeof(v->'target')<>'number' OR (v->>'target')::numeric<=0
      OR (v->>'target')::numeric>100000000 OR (v->>'target')::numeric<>round((v->>'target')::numeric,
      CASE WHEN definition->>'unit'='count' THEN 0 ELSE 2 END)) THEN RAISE EXCEPTION 'category_invalid'; END IF;
 END LOOP;
 INSERT INTO category_reports(id,user_id,restaurant_id,month,definitions,metrics)
 VALUES(operation,actor,restaurant,period,config.definitions,metrics)
 ON CONFLICT(id) DO NOTHING;
 SELECT * INTO old FROM category_reports WHERE id=operation;
 IF old.user_id<>actor OR old.restaurant_id<>restaurant OR old.month<>period OR old.metrics<>metrics
   THEN RAISE EXCEPTION 'category_operation_conflict'; END IF;
END $$;
REVOKE ALL ON FUNCTION configure_categories(BIGINT,UUID,TEXT,INTEGER,UUID,JSONB) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION save_category_report(BIGINT,UUID,TEXT,INTEGER,UUID,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION configure_categories(BIGINT,UUID,TEXT,INTEGER,UUID,JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION save_category_report(BIGINT,UUID,TEXT,INTEGER,UUID,JSONB) TO service_role;
-- Discovered labels are report data, not administrator configuration.
CREATE TABLE category_alerts (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 scope_key TEXT NOT NULL UNIQUE,
 restaurant_id UUID REFERENCES restaurants(id) ON DELETE SET NULL,
 month TEXT NOT NULL,
 category_name TEXT NOT NULL CHECK(length(category_name) BETWEEN 1 AND 60),
 unit TEXT NOT NULL CHECK(unit IN ('amount','count','number')),
 notified_at TIMESTAMPTZ,
 claim_token UUID,
 claimed_until TIMESTAMPTZ
);
ALTER TABLE category_alerts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON category_alerts FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON category_alerts TO service_role;

CREATE FUNCTION save_discovered_category_report(actor BIGINT, restaurant UUID, period TEXT,
 operation UUID, items JSONB, metrics JSONB) RETURNS VOID
LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE old category_reports; item JSONB; ids TEXT[]:='{}'; names TEXT[]:='{}'; k TEXT; v JSONB;
 definition JSONB; cutoff DATE; normalized TEXT;
BEGIN
 IF restaurant IS NOT NULL THEN
   PERFORM id FROM restaurants WHERE id=restaurant FOR SHARE;
   IF NOT EXISTS(SELECT 1 FROM employee_links WHERE user_id=actor AND restaurant_id=restaurant AND status='approved')
      AND NOT EXISTS(SELECT 1 FROM restaurants WHERE id=restaurant AND owner_id=actor)
      THEN RAISE EXCEPTION 'category_forbidden'; END IF;
 END IF;
 IF period !~ '^20[0-9]{2}-(0[1-9]|1[0-2])$' OR period IS NULL THEN RAISE EXCEPTION 'category_invalid'; END IF;
 SELECT * INTO old FROM category_reports WHERE id=operation;
 IF FOUND THEN
   IF old.user_id<>actor OR old.restaurant_id IS DISTINCT FROM restaurant OR old.month<>period
      OR old.metrics<>metrics OR old.definitions<>items THEN RAISE EXCEPTION 'category_operation_conflict'; END IF;
   RETURN;
 END IF;
 IF jsonb_typeof(items)<>'array' OR jsonb_array_length(items) NOT BETWEEN 1 AND 12
    OR items IS NULL OR jsonb_typeof(metrics)<>'object' OR metrics='{}'::jsonb OR metrics IS NULL
    THEN RAISE EXCEPTION 'category_invalid'; END IF;
 FOR item IN SELECT * FROM jsonb_array_elements(items) LOOP
   IF item->>'unit' NOT IN ('amount','count','number') OR item->>'unit' IS NULL
      OR length(item->>'name') NOT BETWEEN 1 AND 60 OR item->>'name' IS NULL
      OR item->>'id' IS NULL OR item->>'id' !~ '^[0-9a-f-]{36}$' OR item->>'id'=ANY(ids)
      OR lower(item->>'name')=ANY(names) THEN RAISE EXCEPTION 'category_invalid'; END IF;
   PERFORM (item->>'id')::uuid;
   ids:=array_append(ids,item->>'id');names:=array_append(names,lower(item->>'name'));
 END LOOP;
 FOR k,v IN SELECT * FROM jsonb_each(metrics) LOOP
   SELECT d INTO definition FROM jsonb_array_elements(items) d WHERE d->>'id'=k;
   IF definition IS NULL OR jsonb_typeof(v->'value')<>'number' OR v->>'value' IS NULL
      OR (v->>'value')::numeric<0 OR (v->>'value')::numeric>100000000
      OR (v->>'value')::numeric<>round((v->>'value')::numeric,CASE WHEN definition->>'unit'='count' THEN 0 ELSE 2 END)
      OR jsonb_typeof(v->'cutoff')<>'string' OR v->>'cutoff' IS NULL
      THEN RAISE EXCEPTION 'category_invalid'; END IF;
   cutoff:=(v->>'cutoff')::date;
   IF to_char(cutoff,'YYYY-MM')<>period OR cutoff>(clock_timestamp() AT TIME ZONE 'Europe/Moscow')::date
      THEN RAISE EXCEPTION 'category_invalid_date'; END IF;
   IF v ? 'target' AND (jsonb_typeof(v->'target')<>'number' OR v->>'target' IS NULL
      OR (v->>'target')::numeric<=0 OR (v->>'target')::numeric>100000000
      OR (v->>'target')::numeric<>round((v->>'target')::numeric,CASE WHEN definition->>'unit'='count' THEN 0 ELSE 2 END))
      THEN RAISE EXCEPTION 'category_invalid'; END IF;
 END LOOP;
 INSERT INTO category_reports(id,user_id,restaurant_id,month,definitions,metrics)
 VALUES(operation,actor,restaurant,period,items,metrics) ON CONFLICT(id) DO NOTHING;
 SELECT * INTO old FROM category_reports WHERE id=operation;
 IF old.user_id<>actor OR old.restaurant_id IS DISTINCT FROM restaurant OR old.month<>period
    OR old.metrics<>metrics OR old.definitions<>items THEN RAISE EXCEPTION 'category_operation_conflict'; END IF;
 FOR item IN SELECT * FROM jsonb_array_elements(items) LOOP
   -- Do not notify about unused headings or familiar built-in category labels.
   IF NOT(metrics ? (item->>'id')) THEN CONTINUE; END IF;
   normalized:=replace(lower(item->>'name'),'ё','е');
   IF normalized IN ('вино','десерты','товарооборот','оборот','коктейли','открытки','двд','dvd') THEN CONTINUE; END IF;
   IF EXISTS(SELECT 1 FROM category_months c CROSS JOIN LATERAL jsonb_array_elements(c.definitions) d
      WHERE c.restaurant_id=restaurant AND replace(lower(d->>'name'),'ё','е')=normalized) THEN CONTINUE; END IF;
   INSERT INTO category_alerts(scope_key,restaurant_id,month,category_name,unit)
   VALUES(COALESCE(restaurant::text,'personal')||':'||normalized,restaurant,period,item->>'name',item->>'unit')
   ON CONFLICT(scope_key) DO NOTHING;
 END LOOP;
END $$;

CREATE FUNCTION claim_category_alerts(claim UUID)
RETURNS TABLE(alert_id UUID,category_name TEXT,period TEXT,unit TEXT,restaurant_name TEXT)
LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
BEGIN
 RETURN QUERY WITH taken AS (
   SELECT a.id FROM category_alerts a WHERE a.notified_at IS NULL
   AND (a.claimed_until IS NULL OR a.claimed_until<clock_timestamp())
   ORDER BY a.id FOR UPDATE SKIP LOCKED LIMIT 10
 ), claimed AS (
   UPDATE category_alerts a SET claim_token=claim,claimed_until=clock_timestamp()+interval '5 minutes'
   WHERE a.id IN (SELECT t.id FROM taken t) RETURNING a.*
 ) SELECT c.id,c.category_name,c.month,c.unit,COALESCE(r.name,'Личный отчёт')
 FROM claimed c LEFT JOIN restaurants r ON r.id=c.restaurant_id;
END $$;
REVOKE ALL ON FUNCTION save_discovered_category_report(BIGINT,UUID,TEXT,UUID,JSONB,JSONB) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION claim_category_alerts(UUID) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION save_discovered_category_report(BIGINT,UUID,TEXT,UUID,JSONB,JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION claim_category_alerts(UUID) TO service_role;

NOTIFY pgrst,'reload schema';
COMMIT;
