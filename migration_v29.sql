-- Permanent restaurant invitations. Apply after v28.
-- Revive only stored hashes; rotated (overwritten) codes cannot be recovered.
BEGIN;
UPDATE public.restaurants SET invite_expires_at=NULL
 WHERE invite_hash IS NOT NULL AND owner_id IS NOT NULL;

CREATE OR REPLACE FUNCTION identity_action(actor BIGINT, action TEXT, args JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path = public AS $$
DECLARE r restaurants; e employee_links;
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
  SELECT * INTO r FROM restaurants WHERE owner_id=actor
   AND (NOT (args ? 'restaurant_id') OR id=(args->>'restaurant_id')::uuid) FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'identity_forbidden'; END IF;
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
   name_key=excluded.name_key,status='pending',requested_at=now(),reviewed_at=NULL,reviewed_by=NULL;
  RETURN (SELECT to_jsonb(x) FROM employee_links x WHERE user_id=actor);
 ELSIF action IN ('approve','reject','revoke') THEN
  SELECT * INTO r FROM restaurants WHERE owner_id=actor
   AND id=(SELECT restaurant_id FROM employee_links WHERE id=(args->>'id')::uuid)
   AND (NOT (args ? 'restaurant_id') OR id=(args->>'restaurant_id')::uuid) FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'identity_forbidden'; END IF;
  SELECT * INTO e FROM employee_links WHERE id=(args->>'id')::uuid AND restaurant_id=r.id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'identity_stale'; END IF;
  IF (action IN ('approve','reject') AND e.status<>'pending') OR (action='revoke' AND e.status<>'approved') THEN
   RAISE EXCEPTION 'identity_stale';
  END IF;
  UPDATE employee_links SET status=CASE action WHEN 'approve' THEN 'approved' WHEN 'reject' THEN 'rejected' ELSE 'revoked' END,
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
NOTIFY pgrst,'reload schema';
COMMIT;
