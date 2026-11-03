-- Restaurant roles: several managers and one director per restaurant. Apply after v30.
-- Owner (the bot administrator who created the restaurant) keeps everything.
-- Director: team, schedule, sales and plans. Manager: team and schedule, no sales.
BEGIN;
ALTER TABLE employee_links ADD COLUMN role TEXT NOT NULL DEFAULT 'staff'
 CHECK(role IN ('staff','manager','director'));
-- Те, кому уже дали право составлять график, становятся менеджерами.
UPDATE employee_links SET role='manager' WHERE can_schedule AND status='approved';
CREATE UNIQUE INDEX employee_links_one_director ON employee_links(restaurant_id)
 WHERE role='director' AND status='approved';

-- Кем человек приходится ресторану: owner, director, manager или NULL.
CREATE FUNCTION restaurant_role(actor BIGINT, restaurant UUID) RETURNS TEXT
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=public AS $$
 SELECT CASE WHEN EXISTS(SELECT 1 FROM restaurants WHERE id=restaurant AND owner_id=actor) THEN 'owner'
  ELSE (SELECT role FROM employee_links WHERE user_id=actor AND restaurant_id=restaurant
        AND status='approved' AND role IN ('manager','director') LIMIT 1) END
$$;
REVOKE ALL ON FUNCTION restaurant_role(BIGINT,UUID) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION restaurant_role(BIGINT,UUID) TO service_role;

CREATE OR REPLACE FUNCTION identity_action(actor BIGINT, action TEXT, args JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path = public AS $$
DECLARE r restaurants; e employee_links; who TEXT;
BEGIN
 IF action='create' THEN
  INSERT INTO restaurants(id,name,owner_id) VALUES ((args->>'id')::uuid,args->>'name',actor)
  ON CONFLICT (owner_id, (lower(name))) DO NOTHING;
  SELECT * INTO r FROM restaurants WHERE owner_id=actor AND lower(name)=lower(args->>'name');
  RETURN jsonb_build_object('id',r.id,'name',r.name);
 ELSIF action='invite' THEN
  IF NOT (args ? 'restaurant_id') AND (SELECT count(*) FROM restaurants WHERE owner_id=actor)>1 THEN
   RAISE EXCEPTION 'identity_select_restaurant';
  END IF;
  SELECT * INTO r FROM restaurants WHERE (args ? 'restaurant_id' AND id=(args->>'restaurant_id')::uuid)
   OR (NOT (args ? 'restaurant_id') AND owner_id=actor) FOR UPDATE;
  IF NOT FOUND OR restaurant_role(actor,r.id) IS NULL THEN RAISE EXCEPTION 'identity_forbidden'; END IF;
  UPDATE restaurants SET invite_hash=args->>'hash',invite_expires_at=NULL WHERE id=r.id;
  RETURN jsonb_build_object('id',r.id,'name',r.name);
 ELSIF action='request' THEN
  -- Serialize simultaneous invitations opened by the same Telegram account.
  PERFORM id FROM users WHERE id=actor FOR UPDATE;
  SELECT * INTO r FROM restaurants WHERE invite_hash=args->>'hash' AND owner_id IS NOT NULL FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'identity_invite_expired'; END IF;
  SELECT * INTO e FROM employee_links WHERE user_id=actor FOR UPDATE;
  IF FOUND AND e.restaurant_id<>r.id THEN RAISE EXCEPTION 'identity_other_restaurant'; END IF;
  IF FOUND AND e.name_key=args->>'name_key' AND e.status IN ('pending','approved') THEN
   RETURN to_jsonb(e);
  END IF;
  INSERT INTO employee_links(id,restaurant_id,user_id,report_name,name_key)
  VALUES ((args->>'id')::uuid,r.id,actor,args->>'name',args->>'name_key')
  ON CONFLICT(user_id) DO UPDATE SET id=excluded.id,report_name=excluded.report_name,
   name_key=excluded.name_key,status='pending',role='staff',requested_at=now(),reviewed_at=NULL,reviewed_by=NULL;
  RETURN (SELECT to_jsonb(x) FROM employee_links x WHERE user_id=actor);
 ELSIF action IN ('approve','reject','revoke') THEN
  SELECT * INTO r FROM restaurants
   WHERE id=(SELECT restaurant_id FROM employee_links WHERE id=(args->>'id')::uuid)
   AND (NOT (args ? 'restaurant_id') OR id=(args->>'restaurant_id')::uuid) FOR UPDATE;
  who:=CASE WHEN FOUND THEN restaurant_role(actor,r.id) END;
  IF who IS NULL THEN RAISE EXCEPTION 'identity_forbidden'; END IF;
  SELECT * INTO e FROM employee_links WHERE id=(args->>'id')::uuid AND restaurant_id=r.id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'identity_stale'; END IF;
  IF (action IN ('approve','reject') AND e.status<>'pending') OR (action='revoke' AND e.status<>'approved') THEN
   RAISE EXCEPTION 'identity_stale';
  END IF;
  -- Менеджер отзывает только рядовых; управляющего отзывает только владелец.
  IF action='revoke' AND ((e.role='manager' AND who='manager') OR (e.role='director' AND who<>'owner')) THEN
   RAISE EXCEPTION 'identity_forbidden';
  END IF;
  UPDATE employee_links SET status=CASE action WHEN 'approve' THEN 'approved' WHEN 'reject' THEN 'rejected' ELSE 'revoked' END,
   role=CASE WHEN action='revoke' THEN 'staff' ELSE role END,
   reviewed_at=now(),reviewed_by=actor WHERE id=e.id RETURNING * INTO e;
  RETURN to_jsonb(e);
 ELSIF action='leave' THEN
  SELECT * INTO r FROM restaurants WHERE id=(SELECT restaurant_id FROM employee_links WHERE user_id=actor) FOR UPDATE;
  DELETE FROM employee_links WHERE user_id=actor AND id=(args->>'id')::uuid;
  IF NOT FOUND THEN RAISE EXCEPTION 'identity_stale'; END IF;
  RETURN '{}'::jsonb;
 ELSE RAISE EXCEPTION 'identity_unknown_action';
 END IF;
END $$;
REVOKE ALL ON FUNCTION identity_action(BIGINT,TEXT,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION identity_action(BIGINT,TEXT,JSONB) TO service_role;

CREATE OR REPLACE FUNCTION configure_categories(actor BIGINT, restaurant UUID, period TEXT, expected INTEGER,
 operation UUID, items JSONB) RETURNS JSONB
LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE previous category_months; item JSONB; names TEXT[]:='{}'; ids TEXT[]:='{}';
BEGIN
 -- Only service_role can invoke. План ставит владелец (с подписью ADMIN_ID в приложении)
 -- или управляющий ресторана; менеджеру это действие закрыто.
 PERFORM id FROM restaurants WHERE id=restaurant FOR UPDATE;
 IF NOT FOUND OR restaurant_role(actor,restaurant) IS DISTINCT FROM 'owner'
    AND restaurant_role(actor,restaurant) IS DISTINCT FROM 'director' THEN
   RAISE EXCEPTION 'category_forbidden'; END IF;
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
REVOKE ALL ON FUNCTION configure_categories(BIGINT,UUID,TEXT,INTEGER,UUID,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION configure_categories(BIGINT,UUID,TEXT,INTEGER,UUID,JSONB) TO service_role;
NOTIFY pgrst,'reload schema';
COMMIT;
