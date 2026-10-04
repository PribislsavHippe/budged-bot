-- Device-only personal finance. Apply after v14, before enabling the button.
BEGIN;

ALTER TABLE users ADD COLUMN IF NOT EXISTS private_money_mode BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE users ADD COLUMN IF NOT EXISTS private_money_public_key JSONB;

CREATE OR REPLACE FUNCTION reject_private_money_write() RETURNS trigger
LANGUAGE plpgsql SET search_path=public AS $$
DECLARE private_enabled BOOLEAN;
BEGIN
 SELECT private_money_mode INTO private_enabled FROM users WHERE id=NEW.user_id FOR SHARE;
 IF private_enabled THEN RAISE EXCEPTION 'private_money_device_only'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER reject_private_money_write_before_insert
BEFORE INSERT ON entries FOR EACH ROW EXECUTE FUNCTION reject_private_money_write();

CREATE FUNCTION activate_private_money(actor BIGINT, expected_ids BIGINT[], public_key JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE current_ids BIGINT[];
BEGIN
 IF public_key->>'kty'<>'RSA' OR public_key->>'alg'<>'RSA-OAEP-256'
    OR public_key->>'n' IS NULL OR public_key->>'e' IS NULL THEN
  RAISE EXCEPTION 'private_key_invalid';
 END IF;
 PERFORM id FROM users WHERE id=actor FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'private_user_missing'; END IF;
 SELECT COALESCE(array_agg(id ORDER BY id),'{}'::BIGINT[]) INTO current_ids
 FROM entries WHERE user_id=actor;
 IF current_ids<>COALESCE(expected_ids,'{}'::BIGINT[]) THEN
  RAISE EXCEPTION 'private_entries_changed';
 END IF;
 DELETE FROM entries WHERE user_id=actor;
 UPDATE users SET private_money_mode=true,private_money_public_key=public_key WHERE id=actor;
END $$;
REVOKE ALL ON FUNCTION activate_private_money(BIGINT,BIGINT[],JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION activate_private_money(BIGINT,BIGINT[],JSONB) TO service_role;

CREATE FUNCTION rotate_private_money_device(actor BIGINT, public_key JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
BEGIN
 IF public_key->>'kty'<>'RSA' OR public_key->>'alg'<>'RSA-OAEP-256'
    OR public_key->>'n' IS NULL OR public_key->>'e' IS NULL THEN
  RAISE EXCEPTION 'private_key_invalid';
 END IF;
 UPDATE users SET private_money_public_key=public_key WHERE id=actor AND private_money_mode=true;
 IF NOT FOUND THEN RAISE EXCEPTION 'private_mode_off'; END IF;
END $$;
REVOKE ALL ON FUNCTION rotate_private_money_device(BIGINT,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION rotate_private_money_device(BIGINT,JSONB) TO service_role;

NOTIFY pgrst, 'reload schema';
COMMIT;
