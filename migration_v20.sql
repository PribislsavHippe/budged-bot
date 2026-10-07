-- Encrypted, device-key-bound recovery copies. Apply after v19 before deploying this code.
BEGIN;

CREATE TABLE IF NOT EXISTS private_money_backups (
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    record_id TEXT NOT NULL CHECK (length(record_id) BETWEEN 1 AND 128),
    payload TEXT NOT NULL CHECK (length(payload) BETWEEN 1 AND 8192),
    key_id TEXT NOT NULL CHECK (length(key_id) = 32),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '14 days'),
    PRIMARY KEY (user_id, record_id)
);
CREATE INDEX IF NOT EXISTS private_money_backups_expiry
    ON private_money_backups(expires_at);
ALTER TABLE private_money_backups ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON private_money_backups FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON private_money_backups TO service_role;

CREATE FUNCTION activate_private_money_with_backups(
    actor BIGINT, expected_ids BIGINT[], public_key JSONB, backups JSONB
) RETURNS VOID LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE current_ids BIGINT[]; backup_ids BIGINT[]; already_private BOOLEAN;
BEGIN
 IF public_key->>'kty'<>'RSA' OR public_key->>'alg'<>'RSA-OAEP-256'
    OR public_key->>'n' IS NULL OR public_key->>'e' IS NULL
    OR jsonb_typeof(backups) IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION 'private_backup_invalid';
 END IF;
 SELECT private_money_mode INTO already_private FROM users WHERE id=actor FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'private_user_missing'; END IF;
 IF already_private THEN RAISE EXCEPTION 'private_already_active'; END IF;
 SELECT COALESCE(array_agg(id ORDER BY id),'{}'::BIGINT[]) INTO current_ids
 FROM entries WHERE user_id=actor;
 SELECT COALESCE(array_agg((item->>'record_id')::BIGINT ORDER BY (item->>'record_id')::BIGINT),'{}'::BIGINT[])
 INTO backup_ids FROM jsonb_array_elements(backups) AS item;
 IF current_ids<>COALESCE(expected_ids,'{}'::BIGINT[]) OR current_ids<>backup_ids THEN
  RAISE EXCEPTION 'private_entries_changed';
 END IF;
 INSERT INTO private_money_backups(user_id,record_id,payload,key_id)
 SELECT actor, item->>'record_id', item->>'payload', md5(public_key->>'n')
 FROM jsonb_array_elements(backups) AS item;
 DELETE FROM entries WHERE user_id=actor;
 UPDATE users SET private_money_mode=true,private_money_public_key=public_key WHERE id=actor;
END $$;
REVOKE ALL ON FUNCTION activate_private_money_with_backups(BIGINT,BIGINT[],JSONB,JSONB)
    FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION activate_private_money_with_backups(BIGINT,BIGINT[],JSONB,JSONB)
    TO service_role;

CREATE FUNCTION store_private_money_backup(
    actor BIGINT, source_id TEXT, sealed TEXT, expected_key_id TEXT
) RETURNS VOID LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE current_key_id TEXT; enabled BOOLEAN;
BEGIN
 SELECT private_money_mode, md5(private_money_public_key->>'n')
 INTO enabled,current_key_id FROM users WHERE id=actor FOR SHARE;
 IF NOT enabled OR current_key_id IS DISTINCT FROM expected_key_id THEN
  RAISE EXCEPTION 'private_key_changed';
 END IF;
 INSERT INTO private_money_backups(user_id,record_id,payload,key_id)
 VALUES(actor,source_id,sealed,expected_key_id)
 ON CONFLICT (user_id,record_id) DO UPDATE
 SET payload=EXCLUDED.payload, key_id=EXCLUDED.key_id,
     expires_at=GREATEST(private_money_backups.expires_at,now()+interval '14 days');
END $$;
REVOKE ALL ON FUNCTION store_private_money_backup(BIGINT,TEXT,TEXT,TEXT)
    FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION store_private_money_backup(BIGINT,TEXT,TEXT,TEXT)
    TO service_role;

NOTIFY pgrst, 'reload schema';
COMMIT;
