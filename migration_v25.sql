-- Durable encrypted Telegram inbox and expiring reminder claims. After v24.
BEGIN;

CREATE TABLE telegram_inbox(
 update_id BIGINT PRIMARY KEY,
 actor_key TEXT NOT NULL CHECK(length(actor_key) BETWEEN 1 AND 100),
 payload TEXT CHECK(length(payload)<=2000000),
 payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),
 received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 finished_at TIMESTAMPTZ,
 batch_id UUID,
 claim_token UUID,
 claimed_until TIMESTAMPTZ,
 next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 attempts INTEGER NOT NULL DEFAULT 0
);
ALTER TABLE telegram_inbox ADD COLUMN effects_done BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE telegram_inbox ADD COLUMN execution_context TEXT CHECK(length(execution_context)<=2000000);
CREATE INDEX telegram_inbox_pending ON telegram_inbox(update_id) WHERE finished_at IS NULL;
ALTER TABLE telegram_inbox ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON telegram_inbox FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON telegram_inbox TO service_role;

CREATE TABLE telegram_actor_state(
 actor_key TEXT PRIMARY KEY,
 payload TEXT NOT NULL CHECK(length(payload)<=2000000)
);
ALTER TABLE telegram_actor_state ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON telegram_actor_state FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON telegram_actor_state TO service_role;

CREATE FUNCTION store_telegram_update(update_no BIGINT,actor_name TEXT,sealed TEXT,digest TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE saved_hash TEXT; finished TIMESTAMPTZ;
BEGIN
 INSERT INTO telegram_inbox(update_id,actor_key,payload,payload_hash)
 VALUES(update_no,actor_name,sealed,digest) ON CONFLICT(update_id) DO NOTHING;
 IF FOUND THEN RETURN true; END IF;
 SELECT payload_hash,finished_at INTO saved_hash,finished FROM telegram_inbox WHERE update_id=update_no;
 IF finished IS NULL AND saved_hash IS DISTINCT FROM digest THEN RAISE EXCEPTION 'telegram_update_changed'; END IF;
 RETURN false;
END $$;
CREATE FUNCTION claim_telegram_batch(ids BIGINT[],batch UUID,token UUID) RETURNS SETOF telegram_inbox
LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE row_count INTEGER; distinct_actors INTEGER;
BEGIN
 IF cardinality(ids) NOT BETWEEN 1 AND 100 OR batch IS NULL OR token IS NULL THEN
  RAISE EXCEPTION 'telegram_batch_invalid';
 END IF;
 PERFORM update_id FROM telegram_inbox WHERE update_id=ANY(ids) ORDER BY update_id FOR UPDATE;
 SELECT count(*),count(DISTINCT actor_key) INTO row_count,distinct_actors FROM telegram_inbox
 WHERE update_id=ANY(ids) AND finished_at IS NULL AND next_attempt_at<=now()
   AND (claimed_until IS NULL OR claimed_until<=now()) AND (batch_id IS NULL OR batch_id=batch);
 IF row_count<>cardinality(ids) OR distinct_actors<>1 THEN RETURN; END IF;
 -- Never process a newer message while an older message of the same person waits.
 IF EXISTS(SELECT 1 FROM telegram_inbox old WHERE old.finished_at IS NULL AND
     old.actor_key=(SELECT actor_key FROM telegram_inbox WHERE update_id=ids[1])
     AND old.update_id<(SELECT min(id) FROM unnest(ids) id) AND NOT old.update_id=ANY(ids)) THEN RETURN; END IF;
 RETURN QUERY UPDATE telegram_inbox SET batch_id=batch,claim_token=token,
  claimed_until=now()+interval '3 minutes',attempts=attempts+1 WHERE update_id=ANY(ids) RETURNING *;
END $$;
CREATE FUNCTION ready_telegram_heads() RETURNS SETOF telegram_inbox
LANGUAGE SQL SECURITY INVOKER SET search_path=public AS $$
 SELECT head.* FROM (SELECT DISTINCT ON (actor_key) * FROM telegram_inbox
   WHERE finished_at IS NULL ORDER BY actor_key,update_id) head
 WHERE head.next_attempt_at<=now() AND (head.claimed_until IS NULL OR head.claimed_until<=now())
 ORDER BY head.update_id LIMIT 50;
$$;
CREATE FUNCTION renew_telegram_batch(batch UUID,token UUID) RETURNS VOID
LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
BEGIN
 UPDATE telegram_inbox SET claimed_until=now()+interval '3 minutes'
 WHERE batch_id=batch AND claim_token=token AND finished_at IS NULL;
END $$;
CREATE FUNCTION finish_telegram_batch(batch UUID,token UUID,succeeded BOOLEAN) RETURNS VOID
LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
BEGIN
 IF succeeded THEN
  UPDATE telegram_inbox SET finished_at=now(),payload=NULL,execution_context=NULL,claim_token=NULL,claimed_until=NULL
  WHERE batch_id=batch AND claim_token=token AND finished_at IS NULL;
 ELSE
  UPDATE telegram_inbox SET claim_token=NULL,claimed_until=NULL,
   next_attempt_at=now()+make_interval(secs=>LEAST(60,attempts*2))
  WHERE batch_id=batch AND claim_token=token AND finished_at IS NULL;
 END IF;
END $$;
REVOKE ALL ON FUNCTION ready_telegram_heads(),store_telegram_update(BIGINT,TEXT,TEXT,TEXT),
 claim_telegram_batch(BIGINT[],UUID,UUID),renew_telegram_batch(UUID,UUID),
 finish_telegram_batch(UUID,UUID,BOOLEAN) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION ready_telegram_heads(),store_telegram_update(BIGINT,TEXT,TEXT,TEXT),
 claim_telegram_batch(BIGINT[],UUID,UUID),renew_telegram_batch(UUID,UUID),
 finish_telegram_batch(UUID,UUID,BOOLEAN) TO service_role;

CREATE FUNCTION remember_telegram_context(update_no BIGINT,token UUID,sealed_context TEXT)
RETURNS TEXT LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE saved TEXT; claimed UUID;
BEGIN
 SELECT execution_context,claim_token INTO saved,claimed FROM telegram_inbox
 WHERE update_id=update_no AND finished_at IS NULL FOR UPDATE;
 IF NOT FOUND OR claimed IS DISTINCT FROM token THEN RAISE EXCEPTION 'telegram_claim_changed'; END IF;
 IF saved IS NULL THEN
  UPDATE telegram_inbox SET execution_context=sealed_context WHERE update_id=update_no;
  saved=sealed_context;
 END IF;
 RETURN saved;
END $$;
REVOKE ALL ON FUNCTION remember_telegram_context(BIGINT,UUID,TEXT) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION remember_telegram_context(BIGINT,UUID,TEXT) TO service_role;

CREATE FUNCTION save_telegram_actor_state(update_no BIGINT,token UUID,sealed_state TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE actor_name TEXT; claimed UUID;
BEGIN
 SELECT actor_key,claim_token INTO actor_name,claimed FROM telegram_inbox
 WHERE update_id=update_no AND finished_at IS NULL FOR UPDATE;
 IF NOT FOUND OR claimed IS DISTINCT FROM token THEN RAISE EXCEPTION 'telegram_claim_changed'; END IF;
 IF sealed_state IS NULL THEN DELETE FROM telegram_actor_state WHERE actor_key=actor_name;
 ELSE
  INSERT INTO telegram_actor_state(actor_key,payload) VALUES(actor_name,sealed_state)
  ON CONFLICT(actor_key) DO UPDATE SET payload=EXCLUDED.payload;
 END IF;
END $$;
REVOKE ALL ON FUNCTION save_telegram_actor_state(BIGINT,UUID,TEXT) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION save_telegram_actor_state(BIGINT,UUID,TEXT) TO service_role;

CREATE FUNCTION apply_telegram_user_action(actor BIGINT,update_no BIGINT,action TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE done BOOLEAN;
BEGIN
 IF action NOT IN ('clear','delete') THEN RAISE EXCEPTION 'telegram_action_invalid'; END IF;
 SELECT effects_done INTO done FROM telegram_inbox WHERE update_id=update_no FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'telegram_update_missing'; END IF;
 IF done THEN RETURN false; END IF;
 PERFORM id FROM users WHERE id=actor FOR UPDATE;
 IF action='clear' AND (SELECT private_money_mode FROM users WHERE id=actor) THEN
  RAISE EXCEPTION 'private_mode_active';
 END IF;
 IF action='delete' THEN DELETE FROM private_money_backups WHERE user_id=actor; END IF;
 DELETE FROM entries WHERE user_id=actor;
 IF action='delete' THEN
  DELETE FROM shifts WHERE user_id=actor;
  DELETE FROM users WHERE id=actor;
  DELETE FROM telegram_actor_state WHERE actor_key=(SELECT actor_key FROM telegram_inbox WHERE update_id=update_no);
 END IF;
 UPDATE telegram_inbox SET effects_done=true WHERE update_id=update_no;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION apply_telegram_user_action(BIGINT,BIGINT,TEXT) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION apply_telegram_user_action(BIGINT,BIGINT,TEXT) TO service_role;

ALTER TABLE shifts ADD COLUMN notice_claims JSONB NOT NULL DEFAULT '{}';
CREATE FUNCTION invalidate_notice_claims() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path=public AS $$
BEGIN
 IF OLD.starts_at IS DISTINCT FROM NEW.starts_at OR OLD.ends_at IS DISTINCT FROM NEW.ends_at THEN
  NEW.notice_claims='{}'::JSONB;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER invalidate_notice_claims_before_update BEFORE UPDATE ON shifts
 FOR EACH ROW EXECUTE FUNCTION invalidate_notice_claims();
CREATE FUNCTION claim_shift_notice(shift BIGINT,notice TEXT,token UUID) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE item shifts%ROWTYPE;
BEGIN
 IF notice NOT IN ('start','end') OR token IS NULL THEN RAISE EXCEPTION 'notice_invalid'; END IF;
 SELECT * INTO item FROM shifts WHERE id=shift FOR UPDATE;
 IF NOT FOUND OR (notice='start' AND item.start_reminder_sent) OR (notice='end' AND item.time_prompt_sent)
  OR (item.notice_claims->notice->>'until')::TIMESTAMPTZ>now() THEN RETURN false; END IF;
 UPDATE shifts SET notice_claims=jsonb_set(notice_claims,ARRAY[notice],
  jsonb_build_object('token',token,'until',now()+interval '3 minutes')) WHERE id=shift;
 RETURN true;
END $$;
CREATE FUNCTION finish_shift_notice(shift BIGINT,notice TEXT,token UUID,delivered BOOLEAN) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE claims JSONB;
BEGIN
 IF notice NOT IN ('start','end') THEN RAISE EXCEPTION 'notice_invalid'; END IF;
 SELECT notice_claims INTO claims FROM shifts WHERE id=shift FOR UPDATE;
 IF NOT FOUND OR (claims->notice->>'token') IS DISTINCT FROM token::TEXT THEN RETURN false; END IF;
 UPDATE shifts SET notice_claims=notice_claims-notice,
   start_reminder_sent=CASE WHEN notice='start' AND delivered THEN true ELSE start_reminder_sent END,
   time_prompt_sent=CASE WHEN notice='end' AND delivered THEN true ELSE time_prompt_sent END,
   time_prompt_at=CASE WHEN notice='end' AND delivered THEN now() ELSE time_prompt_at END WHERE id=shift;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION invalidate_notice_claims(),claim_shift_notice(BIGINT,TEXT,UUID),
 finish_shift_notice(BIGINT,TEXT,UUID,BOOLEAN) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION claim_shift_notice(BIGINT,TEXT,UUID),finish_shift_notice(BIGINT,TEXT,UUID,BOOLEAN) TO service_role;

NOTIFY pgrst,'reload schema';
COMMIT;
