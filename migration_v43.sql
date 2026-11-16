-- Корзина черновиков графика: удаление можно отменить в течение недели (правило владельца 16.11.2026).
-- Apply after v42.
-- Удалённый черновик переезжает в roster_trash вместе с тем, что к нему привязано: чьи ответы о выходных
-- были у этого графика, отметки напоминаний и записи о повторах. «Вернуть» кладёт всё обратно.
-- Через 7 дней корзина чистится сама (purge_roster_trash по расписанию бота).
BEGIN;

CREATE TABLE roster_trash (
  id UUID PRIMARY KEY,
  restaurant_id UUID NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
  start_date DATE NOT NULL,
  end_date DATE NOT NULL,
  deleted_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
  deleted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  roster JSONB NOT NULL,
  answers UUID[] NOT NULL DEFAULT '{}',
  notices JSONB NOT NULL DEFAULT '[]',
  operations JSONB NOT NULL DEFAULT '[]'
);
CREATE INDEX roster_trash_restaurant ON roster_trash(restaurant_id, deleted_at);
ALTER TABLE roster_trash ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON roster_trash FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, DELETE ON roster_trash TO service_role;

CREATE FUNCTION trash_roster(actor BIGINT, p_id UUID) RETURNS JSONB
LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE r rosters; at TIMESTAMPTZ := now();
BEGIN
 SELECT * INTO r FROM rosters WHERE id=p_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'roster_missing'; END IF;
 IF r.published_at IS NOT NULL THEN RAISE EXCEPTION 'roster_published'; END IF;
 INSERT INTO roster_trash(id,restaurant_id,start_date,end_date,deleted_by,deleted_at,roster,answers,notices,operations)
 VALUES(r.id,r.restaurant_id,r.start_date,r.end_date,actor,at,to_jsonb(r),
   COALESCE((SELECT array_agg(link_id) FROM day_off_requests WHERE roster_id=r.id),'{}'),
   COALESCE((SELECT jsonb_agg(to_jsonb(n)) FROM roster_wish_notices n WHERE n.roster_id=r.id),'[]'),
   COALESCE((SELECT jsonb_agg(to_jsonb(o)) FROM roster_wish_operations o WHERE o.roster_id=r.id),'[]'));
 -- Ответы сотрудников остаются в day_off_requests (roster_id становится NULL), отметки — в корзине.
 DELETE FROM rosters WHERE id=r.id;
 RETURN jsonb_build_object('id',r.id,'deleted_at',at);
END $$;

CREATE FUNCTION restore_roster(actor BIGINT, p_id UUID) RETURNS JSONB
LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE t roster_trash; restored rosters;
BEGIN
 SELECT * INTO t FROM roster_trash WHERE id=p_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'roster_trash_missing'; END IF;
 -- Пересечение с графиком, созданным за это время, остановит возврат (rosters_no_overlap, уникальная дата начала).
 INSERT INTO rosters SELECT * FROM jsonb_populate_record(NULL::rosters, t.roster) RETURNING * INTO restored;
 UPDATE day_off_requests SET roster_id=t.id
  WHERE link_id=ANY(t.answers) AND restaurant_id=t.restaurant_id AND start_date=t.start_date AND roster_id IS NULL;
 INSERT INTO roster_wish_notices SELECT x.* FROM jsonb_populate_recordset(NULL::roster_wish_notices, t.notices) x
  WHERE EXISTS (SELECT 1 FROM employee_links e WHERE e.id=x.link_id) ON CONFLICT DO NOTHING;
 INSERT INTO roster_wish_operations SELECT x.* FROM jsonb_populate_recordset(NULL::roster_wish_operations, t.operations) x
  WHERE EXISTS (SELECT 1 FROM employee_links e WHERE e.id=x.link_id) ON CONFLICT DO NOTHING;
 DELETE FROM roster_trash WHERE id=t.id;
 RETURN to_jsonb(restored);
END $$;

CREATE FUNCTION purge_roster_trash() RETURNS INTEGER
LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE gone INTEGER;
BEGIN
 DELETE FROM roster_trash WHERE deleted_at < now() - interval '7 days';
 GET DIAGNOSTICS gone=ROW_COUNT;
 RETURN gone;
END $$;

REVOKE ALL ON FUNCTION trash_roster(BIGINT,UUID), restore_roster(BIGINT,UUID), purge_roster_trash()
 FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION trash_roster(BIGINT,UUID), restore_roster(BIGINT,UUID), purge_roster_trash() TO service_role;

NOTIFY pgrst, 'reload schema';
COMMIT;
