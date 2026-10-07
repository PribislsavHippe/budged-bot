-- Return from device-only money mode without losing the existing journal.
-- Apply after v20 before deploying the exit control.
BEGIN;

ALTER TABLE users ADD COLUMN IF NOT EXISTS private_money_exit_operation UUID;
ALTER TABLE users ADD COLUMN IF NOT EXISTS private_money_exit_count INTEGER;

CREATE FUNCTION deactivate_private_money(
    actor BIGINT, operation UUID, expected_public_key JSONB, items JSONB, receipts JSONB
) RETURNS INTEGER LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE enabled BOOLEAN; prior_operation UUID; prior_count INTEGER;
        current_key JSONB; imported_count INTEGER;
BEGIN
 IF operation IS NULL OR jsonb_typeof(items) IS DISTINCT FROM 'array'
    OR jsonb_typeof(receipts) IS DISTINCT FROM 'array'
    OR jsonb_array_length(items)>5000 OR jsonb_array_length(receipts)>10000 THEN
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
      tip_percent NUMERIC,work_date DATE,created_at TIMESTAMPTZ)
  WHERE item.id IS NULL OR length(item.id) NOT BETWEEN 1 AND 128
     OR item.kind IS NULL OR item.kind NOT IN ('income','expense','adjustment','accrual')
     OR item.account IS NULL OR item.account NOT IN ('cash','card','pending')
     OR (item.kind='accrual') IS DISTINCT FROM (item.account='pending')
     OR item.signed_amount IS NULL OR abs(item.signed_amount)>10000000
     OR item.signed_amount<>round(item.signed_amount,2)
     OR item.category IS NULL OR length(item.category) NOT BETWEEN 1 AND 100
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

REVOKE ALL ON FUNCTION deactivate_private_money(BIGINT,UUID,JSONB,JSONB,JSONB)
  FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION deactivate_private_money(BIGINT,UUID,JSONB,JSONB,JSONB)
  TO service_role;

NOTIFY pgrst, 'reload schema';
COMMIT;
