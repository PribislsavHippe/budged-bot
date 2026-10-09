-- Finance integrity: versioned transfers and atomic Telegram batches. After v23.
BEGIN;

-- Repair the original tables even if the old installation skipped v11.
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE shifts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON users,entries,shifts FROM PUBLIC,anon,authenticated;
GRANT ALL ON users,entries,shifts TO service_role;
GRANT USAGE,SELECT ON SEQUENCE entries_id_seq,shifts_id_seq TO service_role;

ALTER TABLE entries ADD COLUMN IF NOT EXISTS revision BIGINT NOT NULL DEFAULT 1;
ALTER TABLE entries ADD COLUMN IF NOT EXISTS source_payload JSONB;
UPDATE entries SET source_key='miniapp-expense:'||client_operation_id::TEXT
 WHERE source_key IS NULL AND client_operation_id IS NOT NULL;
UPDATE entries SET source_payload=jsonb_build_object('kind',kind,'account',account,
 'signed_amount',signed_amount,'category',category,'note',note,'work_date',work_date,
 'order_amount',order_amount,'tip_percent',tip_percent) WHERE source_key IS NOT NULL;


CREATE TABLE money_source_receipts(
 user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 source_key TEXT NOT NULL CHECK(length(source_key) BETWEEN 1 AND 128),
 PRIMARY KEY(user_id,source_key)
);
ALTER TABLE money_source_receipts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON money_source_receipts FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,DELETE ON money_source_receipts TO service_role;
INSERT INTO money_source_receipts SELECT user_id,source_key FROM entries WHERE source_key IS NOT NULL
 ON CONFLICT DO NOTHING;
INSERT INTO money_source_receipts SELECT user_id,record_id FROM private_money_backups
 WHERE record_id ~ '^telegram:[0-9]+:[0-9]+:[0-9]+$' ON CONFLICT DO NOTHING;

CREATE FUNCTION guard_money_entry() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE owner_id BIGINT; enabled BOOLEAN;
BEGIN
 IF TG_OP='DELETE' THEN owner_id=OLD.user_id;
 ELSE owner_id=NEW.user_id; END IF;
 IF TG_OP='UPDATE' AND NEW.user_id IS DISTINCT FROM OLD.user_id THEN
  RAISE EXCEPTION 'entry_owner_changed';
 END IF;
 -- All entry writes participate in the same owner lock as activation/exit.
 SELECT private_money_mode INTO enabled FROM users WHERE id=owner_id FOR UPDATE;
 IF TG_OP<>'DELETE' THEN
  IF enabled THEN RAISE EXCEPTION 'private_mode_active'; END IF;
  IF TG_OP='UPDATE' THEN
   IF NEW.source_key IS DISTINCT FROM OLD.source_key THEN RAISE EXCEPTION 'entry_source_changed'; END IF;
   NEW.revision=OLD.revision+1;NEW.source_payload=OLD.source_payload;
  ELSE
   NEW.revision=1;
   IF NEW.source_key IS NULL AND NEW.client_operation_id IS NOT NULL THEN
    NEW.source_key='miniapp-expense:'||NEW.client_operation_id::TEXT;
   END IF;
   IF NEW.source_key IS NOT NULL THEN
    IF EXISTS(SELECT 1 FROM money_source_receipts WHERE user_id=owner_id AND source_key=NEW.source_key) THEN
     RAISE EXCEPTION 'money_source_already_processed';
    END IF;
    INSERT INTO money_source_receipts VALUES(owner_id,NEW.source_key);
   END IF;
   NEW.source_payload=CASE WHEN NEW.source_key IS NOT NULL THEN jsonb_build_object(
    'kind',NEW.kind,'account',NEW.account,'signed_amount',NEW.signed_amount,
    'category',NEW.category,'note',NEW.note,'work_date',NEW.work_date,
    'order_amount',NEW.order_amount,'tip_percent',NEW.tip_percent) ELSE NULL END;
  END IF;
  RETURN NEW;
 END IF;
 RETURN OLD;
END $$;
CREATE TRIGGER entries_money_guard BEFORE INSERT OR UPDATE OR DELETE ON entries
 FOR EACH ROW EXECUTE FUNCTION guard_money_entry();
REVOKE ALL ON FUNCTION guard_money_entry() FROM PUBLIC,anon,authenticated;

DROP FUNCTION add_miniapp_expense(BIGINT,UUID,NUMERIC,TEXT);
CREATE FUNCTION add_miniapp_expense(actor BIGINT,operation UUID,amount NUMERIC,
 category_name TEXT,work_day DATE DEFAULT NULL) RETURNS JSONB
LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE e entries; enabled BOOLEAN; original JSONB;
BEGIN
 IF operation IS NULL OR amount IS NULL OR amount<=0 OR amount>10000000
    OR amount<>round(amount,2) OR category_name IS NULL
    OR length(btrim(category_name)) NOT BETWEEN 1 AND 60 THEN
  RAISE EXCEPTION 'expense_invalid';
 END IF;
 SELECT private_money_mode INTO enabled FROM users WHERE id=actor FOR UPDATE;
 IF NOT FOUND OR enabled THEN RAISE EXCEPTION 'private_mode_active'; END IF;
 SELECT * INTO e FROM entries WHERE user_id=actor AND client_operation_id=operation;
 IF FOUND THEN
  original=e.source_payload;
  IF original->>'kind'<>'expense' OR original->>'account'<>'cash'
     OR (original->>'signed_amount')::NUMERIC<>-amount
     OR original->>'category'<>category_name
     OR (work_day IS NOT NULL AND original->>'work_date'<>work_day::TEXT) THEN
   RAISE EXCEPTION 'expense_conflict';
  END IF;
  RETURN jsonb_build_object('id',e.id);
 END IF;
 IF EXISTS(SELECT 1 FROM money_source_receipts WHERE user_id=actor
           AND source_key='miniapp-expense:'||operation::TEXT) THEN
  RETURN jsonb_build_object('already_processed',true);
 END IF;
 INSERT INTO entries(user_id,kind,account,signed_amount,category,note,client_operation_id,work_date)
 VALUES(actor,'expense','cash',-amount,category_name,'трата из миниаппа',operation,
   COALESCE(work_day,((now() AT TIME ZONE 'Europe/Moscow')-interval '6 hours')::DATE))
 RETURNING * INTO e;
 RETURN jsonb_build_object('id',e.id);
END $$;
REVOKE ALL ON FUNCTION add_miniapp_expense(BIGINT,UUID,NUMERIC,TEXT,DATE) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION add_miniapp_expense(BIGINT,UUID,NUMERIC,TEXT,DATE) TO service_role;

DROP FUNCTION activate_private_money_with_backups(BIGINT,BIGINT[],JSONB,JSONB);
CREATE FUNCTION activate_private_money_with_backups(
 actor BIGINT,expected_ids BIGINT[],expected_versions JSONB,public_key JSONB,backups JSONB
) RETURNS VOID LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE current_ids BIGINT[]; backup_ids BIGINT[]; enabled BOOLEAN;
BEGIN
 IF public_key->>'kty'<>'RSA' OR public_key->>'alg'<>'RSA-OAEP-256'
    OR public_key->>'n' IS NULL OR public_key->>'e' IS NULL
    OR jsonb_typeof(backups) IS DISTINCT FROM 'array'
    OR jsonb_typeof(expected_versions) IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION 'private_backup_invalid';
 END IF;
 SELECT private_money_mode INTO enabled FROM users WHERE id=actor FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'private_user_missing'; END IF;
 IF enabled THEN RAISE EXCEPTION 'private_already_active'; END IF;
 SELECT COALESCE(array_agg(id ORDER BY id),'{}'::BIGINT[]) INTO current_ids FROM entries WHERE user_id=actor;
 SELECT COALESCE(array_agg((item->>'record_id')::BIGINT ORDER BY (item->>'record_id')::BIGINT),'{}'::BIGINT[])
 INTO backup_ids FROM jsonb_array_elements(backups) item;
 IF current_ids<>COALESCE(expected_ids,'{}'::BIGINT[]) OR current_ids<>backup_ids
    OR jsonb_array_length(expected_versions)<>cardinality(current_ids)
    OR EXISTS(SELECT 1 FROM entries e WHERE e.user_id=actor AND NOT EXISTS(
       SELECT 1 FROM jsonb_to_recordset(expected_versions) v(id BIGINT,revision BIGINT)
       WHERE v.id=e.id AND v.revision=e.revision)) THEN
  RAISE EXCEPTION 'private_entries_changed';
 END IF;
 INSERT INTO private_money_backups(user_id,record_id,payload,key_id)
 SELECT actor,item->>'record_id',item->>'payload',md5(public_key->>'n') FROM jsonb_array_elements(backups) item;
 DELETE FROM entries WHERE user_id=actor;
 UPDATE users SET private_money_mode=true,private_money_public_key=public_key WHERE id=actor;
END $$;
REVOKE ALL ON FUNCTION activate_private_money_with_backups(BIGINT,BIGINT[],JSONB,JSONB,JSONB)
 FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION activate_private_money_with_backups(BIGINT,BIGINT[],JSONB,JSONB,JSONB) TO service_role;

CREATE FUNCTION add_money_entries(actor BIGINT,items JSONB) RETURNS JSONB
LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE enabled BOOLEAN; result JSONB;
BEGIN
 SELECT private_money_mode INTO enabled FROM users WHERE id=actor FOR UPDATE;
 IF NOT FOUND OR enabled THEN RAISE EXCEPTION 'private_mode_active'; END IF;
 IF jsonb_typeof(items) IS DISTINCT FROM 'array' OR jsonb_array_length(items) NOT BETWEEN 1 AND 32 THEN
  RAISE EXCEPTION 'money_batch_invalid';
 END IF;
 IF (SELECT count(DISTINCT item->>'source_key') FROM jsonb_array_elements(items) item)<>jsonb_array_length(items)
 OR EXISTS(SELECT 1 FROM jsonb_to_recordset(items) x(kind TEXT,account TEXT,signed_amount NUMERIC,
             category TEXT,note TEXT,source_key TEXT,work_date DATE)
    WHERE x.kind IS NULL OR x.kind NOT IN ('income','expense','accrual')
       OR x.account IS NULL OR x.account NOT IN ('cash','card','pending')
       OR (x.kind='accrual') IS DISTINCT FROM (x.account='pending')
       OR x.signed_amount IS NULL OR abs(x.signed_amount)>10000000
       OR x.signed_amount<>round(x.signed_amount,2)
       OR (x.kind='expense' AND x.signed_amount>=0) OR (x.kind<>'expense' AND x.signed_amount<=0)
       OR x.category IS NULL OR length(x.category) NOT BETWEEN 1 AND 100
       OR length(COALESCE(x.note,''))>4096 OR x.work_date IS NULL
       OR x.source_key IS NULL OR length(x.source_key) NOT BETWEEN 1 AND 128) THEN
  RAISE EXCEPTION 'money_batch_invalid';
 END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(items) x
    JOIN money_source_receipts r ON r.user_id=actor AND r.source_key=x->>'source_key'
    WHERE NOT EXISTS(SELECT 1 FROM entries e WHERE e.user_id=actor AND e.source_key=r.source_key)) THEN
  RETURN '[]'::JSONB;
 END IF;
 IF EXISTS(SELECT 1 FROM jsonb_to_recordset(items) x(kind TEXT,account TEXT,signed_amount NUMERIC,
           category TEXT,note TEXT,source_key TEXT,work_date DATE)
     JOIN entries e ON e.user_id=actor AND e.source_key=x.source_key
     WHERE e.source_payload IS DISTINCT FROM jsonb_build_object('kind',x.kind,'account',x.account,
       'signed_amount',x.signed_amount,'category',x.category,'note',x.note,'work_date',x.work_date,
       'order_amount',NULL,'tip_percent',NULL)) THEN
  RAISE EXCEPTION 'source_conflict';
 END IF;
 INSERT INTO entries(user_id,kind,account,signed_amount,category,note,source_key,work_date)
 SELECT actor,x.kind,x.account,x.signed_amount,x.category,x.note,x.source_key,x.work_date
 FROM jsonb_to_recordset(items) x(kind TEXT,account TEXT,signed_amount NUMERIC,
                                category TEXT,note TEXT,source_key TEXT,work_date DATE)
 WHERE NOT EXISTS(SELECT 1 FROM entries e WHERE e.user_id=actor AND e.source_key=x.source_key)
 ON CONFLICT DO NOTHING;
 SELECT jsonb_agg(to_jsonb(e) ORDER BY x.ordinality) INTO result
 FROM jsonb_array_elements(items) WITH ORDINALITY x(item,ordinality)
 JOIN entries e ON e.user_id=actor AND e.source_key=x.item->>'source_key';
 IF jsonb_array_length(result)<>jsonb_array_length(items) THEN RAISE EXCEPTION 'money_batch_incomplete'; END IF;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION add_money_entries(BIGINT,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION add_money_entries(BIGINT,JSONB) TO service_role;

CREATE FUNCTION undo_money_batch(actor BIGINT,batch_prefix TEXT) RETURNS INTEGER
LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE removed INTEGER;
BEGIN
 IF batch_prefix !~ '^telegram:[0-9]+:[0-9]+:$' THEN RAISE EXCEPTION 'money_batch_invalid'; END IF;
 PERFORM id FROM users WHERE id=actor FOR UPDATE;
 DELETE FROM entries WHERE user_id=actor AND source_key LIKE batch_prefix||'%';
 GET DIAGNOSTICS removed=ROW_COUNT;
 RETURN removed;
END $$;
REVOKE ALL ON FUNCTION undo_money_batch(BIGINT,TEXT) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION undo_money_batch(BIGINT,TEXT) TO service_role;

CREATE OR REPLACE FUNCTION deactivate_private_money(
    actor BIGINT, operation UUID, expected_public_key JSONB, items JSONB, receipts JSONB
) RETURNS INTEGER LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE enabled BOOLEAN; prior_operation UUID; prior_count INTEGER;
        current_key JSONB; imported_count INTEGER;
BEGIN
 IF operation IS NULL OR jsonb_typeof(items) IS DISTINCT FROM 'array'
    OR jsonb_typeof(receipts) IS DISTINCT FROM 'array'
    OR jsonb_array_length(items)>100000 OR jsonb_array_length(receipts)>1000000
    OR octet_length(items::TEXT)+octet_length(receipts::TEXT)>6*1024*1024 THEN
  RAISE EXCEPTION 'private_exit_invalid';
 END IF;
 SELECT private_money_mode,private_money_exit_operation,private_money_exit_count,
        private_money_public_key
 INTO enabled,prior_operation,prior_count,current_key
 FROM users WHERE id=actor FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'private_user_missing'; END IF;
 IF NOT enabled THEN
  IF prior_operation=operation THEN RETURN prior_count; END IF;
  RAISE EXCEPTION 'private_mode_off';
 END IF;
 IF prior_operation=operation THEN RAISE EXCEPTION 'private_exit_operation_reused'; END IF;
 IF current_key IS DISTINCT FROM expected_public_key THEN
  RAISE EXCEPTION 'private_key_changed';
 END IF;
 IF EXISTS (SELECT 1 FROM entries WHERE user_id=actor) THEN
  RAISE EXCEPTION 'private_server_entries_present';
 END IF;
 IF EXISTS (
  SELECT 1 FROM jsonb_to_recordset(items) AS item(id TEXT,kind TEXT,account TEXT,
      signed_amount NUMERIC,category TEXT,note TEXT,order_amount NUMERIC,
      tip_percent NUMERIC,work_date DATE,created_at TIMESTAMPTZ,source_key TEXT)
  WHERE item.id IS NULL OR length(item.id) NOT BETWEEN 1 AND 128
     OR item.kind IS NULL OR item.kind NOT IN ('income','expense','adjustment','accrual')
     OR item.account IS NULL OR item.account NOT IN ('cash','card','pending')
     OR (item.kind='accrual') IS DISTINCT FROM (item.account='pending')
     OR item.signed_amount IS NULL OR abs(item.signed_amount)>10000000
     OR item.signed_amount<>round(item.signed_amount,2)
     OR item.category IS NULL OR length(item.category) NOT BETWEEN 1 AND 100
     OR (item.source_key IS NOT NULL AND length(item.source_key) NOT BETWEEN 1 AND 128)
     OR item.work_date IS NULL OR item.created_at IS NULL
 ) OR (SELECT count(DISTINCT item->>'id') FROM jsonb_array_elements(items) item)
      <>jsonb_array_length(items) THEN
  RAISE EXCEPTION 'private_exit_entries_invalid';
 END IF;
 IF EXISTS (
  SELECT 1 FROM private_money_backups b
  WHERE b.user_id=actor AND b.expires_at>now()
    AND b.key_id=md5(current_key->>'n')
    AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements_text(receipts) r
                    WHERE r.value=b.record_id)
 ) THEN
  RAISE EXCEPTION 'private_backups_pending';
 END IF;
 UPDATE users SET private_money_mode=false,private_money_public_key=NULL,
     private_money_exit_operation=operation,
     private_money_exit_count=jsonb_array_length(items)
 WHERE id=actor;
 -- Remember local operation IDs too: a pending UI retry after leaving private
 -- mode must not create a second copy of a record just imported from the phone.
 INSERT INTO money_source_receipts(user_id,source_key)
 SELECT actor,item.source_key FROM jsonb_to_recordset(items) item(source_key TEXT)
 WHERE item.source_key IS NOT NULL ON CONFLICT DO NOTHING;
 INSERT INTO money_source_receipts(user_id,source_key)
 SELECT actor,r.value FROM jsonb_array_elements_text(receipts) r
 WHERE r.value ~ '^(calendar|calendar-expense|miniapp-expense):[0-9a-f-]{36}$'
 ON CONFLICT DO NOTHING;
 INSERT INTO entries(user_id,kind,account,signed_amount,category,note,
                     order_amount,tip_percent,work_date,created_at,source_key)
 SELECT actor,item.kind,item.account,item.signed_amount,item.category,item.note,
        item.order_amount,item.tip_percent,item.work_date,item.created_at,
        'private-exit:'||operation::TEXT||':'||item.id
 FROM jsonb_to_recordset(items) AS item(id TEXT,kind TEXT,account TEXT,
      signed_amount NUMERIC,category TEXT,note TEXT,order_amount NUMERIC,
      tip_percent NUMERIC,work_date DATE,created_at TIMESTAMPTZ);
 GET DIAGNOSTICS imported_count=ROW_COUNT;
 IF imported_count<>jsonb_array_length(items) THEN
  RAISE EXCEPTION 'private_exit_count_mismatch';
 END IF;
 DELETE FROM private_money_backups WHERE user_id=actor;
 RETURN imported_count;
END $$;

CREATE OR REPLACE FUNCTION store_private_money_backup(
 actor BIGINT,source_id TEXT,sealed TEXT,expected_key_id TEXT
) RETURNS VOID LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE current_key_id TEXT; enabled BOOLEAN;
BEGIN
 SELECT private_money_mode,md5(private_money_public_key->>'n') INTO enabled,current_key_id
 FROM users WHERE id=actor FOR UPDATE;
 IF NOT enabled OR current_key_id IS DISTINCT FROM expected_key_id THEN RAISE EXCEPTION 'private_key_changed'; END IF;
 IF source_id ~ '^telegram:[0-9]+:[0-9]+:[0-9]+$' THEN
  INSERT INTO money_source_receipts VALUES(actor,source_id) ON CONFLICT DO NOTHING;
  IF NOT FOUND THEN RETURN; END IF;
 END IF;
 INSERT INTO private_money_backups(user_id,record_id,payload,key_id)
 VALUES(actor,source_id,sealed,expected_key_id) ON CONFLICT(user_id,record_id) DO NOTHING;
END $$;
NOTIFY pgrst,'reload schema';
COMMIT;
