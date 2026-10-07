-- Move an approved employee between restaurants owned by the same administrator.
-- Sales and plans stay personal; the new restaurant sees records created after the move.
BEGIN;
CREATE FUNCTION identity_transfer(actor BIGINT, link_id UUID, source_id UUID, target_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE member employee_links; owned_count INTEGER;
BEGIN
 IF source_id IS NULL OR target_id IS NULL OR source_id=target_id THEN
  RAISE EXCEPTION 'identity_invalid_target';
 END IF;
 -- Match identity_action's restaurant-first lock order; lock both in stable order.
 PERFORM id FROM restaurants WHERE id IN (source_id,target_id) ORDER BY id FOR UPDATE;
 SELECT count(*) INTO owned_count FROM restaurants
  WHERE id IN (source_id,target_id) AND owner_id=actor;
 IF owned_count<>2 THEN RAISE EXCEPTION 'identity_forbidden'; END IF;
 SELECT * INTO member FROM employee_links
  WHERE id=link_id AND restaurant_id=source_id FOR UPDATE;
 IF NOT FOUND OR member.status<>'approved' THEN RAISE EXCEPTION 'identity_stale'; END IF;
 UPDATE employee_links SET restaurant_id=target_id,requested_at=now(),
  reviewed_at=now(),reviewed_by=actor WHERE id=link_id RETURNING * INTO member;
 RETURN to_jsonb(member);
END $$;
REVOKE ALL ON FUNCTION identity_transfer(BIGINT,UUID,UUID,UUID) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION identity_transfer(BIGINT,UUID,UUID,UUID) TO service_role;
NOTIFY pgrst,'reload schema';
COMMIT;
