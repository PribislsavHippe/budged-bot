-- Исправления из ревью 15.11.2026. Apply after v40.
-- 1. График: какая публикация уже разложена по личным календарям, чтобы запуск бота не затирал личные правки.
-- 2. Повторная заявка в ресторан: новый id сотрудника переносится в его выходные, пожелания, правила и графики.
-- 3. Очередь Telegram: сообщение, которое не удаётся обработать, больше не блокирует человека навсегда.
BEGIN;

-- 1. Публикация, смены которой уже записаны в личные календари. Всё, что опубликовано до этой миграции,
-- бот раскладывал при каждом запуске, поэтому считается разложенным.
ALTER TABLE rosters ADD COLUMN shifts_synced_at TIMESTAMPTZ;
UPDATE rosters SET shifts_synced_at=published_at WHERE published_at IS NOT NULL;

-- 2. Ссылки на сотрудника следуют за сменой его id (повторная заявка после отзыва, отказа или с другим именем).
ALTER TABLE day_off_requests DROP CONSTRAINT day_off_requests_link_id_fkey,
 ADD CONSTRAINT day_off_requests_link_id_fkey FOREIGN KEY(link_id) REFERENCES employee_links(id) ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE weekday_wishes DROP CONSTRAINT weekday_wishes_link_id_fkey,
 ADD CONSTRAINT weekday_wishes_link_id_fkey FOREIGN KEY(link_id) REFERENCES employee_links(id) ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE roster_wish_operations DROP CONSTRAINT roster_wish_operations_link_id_fkey,
 ADD CONSTRAINT roster_wish_operations_link_id_fkey FOREIGN KEY(link_id) REFERENCES employee_links(id) ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE roster_wish_notices DROP CONSTRAINT roster_wish_notices_link_id_fkey,
 ADD CONSTRAINT roster_wish_notices_link_id_fkey FOREIGN KEY(link_id) REFERENCES employee_links(id) ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE shift_time_rules DROP CONSTRAINT shift_time_rules_link_id_fkey,
 ADD CONSTRAINT shift_time_rules_link_id_fkey FOREIGN KEY(link_id) REFERENCES employee_links(id) ON DELETE CASCADE ON UPDATE CASCADE;

CREATE OR REPLACE FUNCTION identity_action(actor BIGINT, action TEXT, args JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path = public AS $$
DECLARE r restaurants; e employee_links; who TEXT; old_id UUID;
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
  -- Reopening an invitation must preserve the confirmed link, report name and role.
  IF FOUND AND (e.status='approved' OR (e.status='pending' AND e.name_key=args->>'name_key')) THEN
   RETURN to_jsonb(e);
  END IF;
  old_id:=CASE WHEN FOUND THEN e.id END;
  -- A different Telegram account needs its own report name or code.
  IF EXISTS (SELECT 1 FROM employee_links
             WHERE restaurant_id=r.id AND name_key=args->>'name_key'
               AND status='approved' AND user_id<>actor) THEN
   RAISE EXCEPTION 'identity_name_taken' USING ERRCODE='23505';
  END IF;
  -- A new id keeps old approval buttons stale. Child rows follow it (ON UPDATE CASCADE).
  INSERT INTO employee_links(id,restaurant_id,user_id,report_name,name_key)
  VALUES ((args->>'id')::uuid,r.id,actor,args->>'name',args->>'name_key')
  ON CONFLICT(user_id) DO UPDATE SET id=excluded.id,report_name=excluded.report_name,
   name_key=excluded.name_key,status='pending',role='staff',requested_at=now(),reviewed_at=NULL,reviewed_by=NULL;
  -- Rosters keep the person by link id inside JSON: carry the new id there as well.
  IF old_id IS NOT NULL AND old_id<>(args->>'id')::uuid THEN
   UPDATE rosters SET draft=replace(draft::text,old_id::text,args->>'id')::jsonb,
    published=CASE WHEN published IS NULL THEN NULL ELSE replace(published::text,old_id::text,args->>'id')::jsonb END
   WHERE restaurant_id=r.id AND (draft::text LIKE '%'||old_id::text||'%' OR published::text LIKE '%'||old_id::text||'%');
  END IF;
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

-- 3. Сообщение, которое не обработалось за 10 попыток и 30 минут, откладывается: следующие сообщения
-- человека снова обрабатываются. Порог по времени не даёт выбросить записи во время короткого сбоя базы.
ALTER TABLE telegram_inbox ADD COLUMN abandoned_at TIMESTAMPTZ;
DROP FUNCTION finish_telegram_batch(UUID,UUID,BOOLEAN);
CREATE FUNCTION finish_telegram_batch(batch UUID,token UUID,succeeded BOOLEAN) RETURNS INTEGER
LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE given_up INTEGER:=0;
BEGIN
 IF succeeded THEN
  UPDATE telegram_inbox SET finished_at=now(),payload=NULL,execution_context=NULL,claim_token=NULL,claimed_until=NULL
  WHERE batch_id=batch AND claim_token=token AND finished_at IS NULL;
 ELSE
  UPDATE telegram_inbox SET finished_at=now(),abandoned_at=now(),payload=NULL,execution_context=NULL,
   claim_token=NULL,claimed_until=NULL
  WHERE batch_id=batch AND claim_token=token AND finished_at IS NULL
    AND attempts>=10 AND received_at<=now()-interval '30 minutes';
  GET DIAGNOSTICS given_up=ROW_COUNT;
  UPDATE telegram_inbox SET claim_token=NULL,claimed_until=NULL,
   next_attempt_at=now()+make_interval(secs=>LEAST(60,attempts*2))
  WHERE batch_id=batch AND claim_token=token AND finished_at IS NULL;
 END IF;
 RETURN given_up;
END $$;
REVOKE ALL ON FUNCTION finish_telegram_batch(UUID,UUID,BOOLEAN) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION finish_telegram_batch(UUID,UUID,BOOLEAN) TO service_role;

NOTIFY pgrst,'reload schema';
COMMIT;
