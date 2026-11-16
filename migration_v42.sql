-- Исправления P2 из ревью 15.11.2026. Apply after v41.
-- 1. Напоминание о пожеланиях, которое не отправилось, повторяется (до 3 попыток, не чаще раза в час).
-- 2. Рассылка владельца запоминает, кому уже отправлена: перезапуск не шлёт её второй раз.
-- 3. Перенос сотрудника в другой ресторан переносит его пожелания по дням недели и правила времени.
-- 4. Графики одного ресторана не пересекаются по датам даже при одновременном создании.
BEGIN;

-- 1. Напоминания о пожеланиях.
ALTER TABLE roster_wish_notices ADD COLUMN attempts SMALLINT NOT NULL DEFAULT 1;

CREATE OR REPLACE FUNCTION claim_roster_wish_notice(p_now timestamptz, p_roster uuid)
RETURNS jsonb LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE
  local_time timestamp := p_now AT TIME ZONE 'Europe/Moscow';
  candidate record;
BEGIN
  IF local_time::time < time '10:00' OR local_time::time >= time '20:00' THEN RETURN NULL; END IF;
  SELECT r.id AS roster_id, r.start_date, r.end_date, e.id AS link_id, e.user_id,
         CASE WHEN first.sent_at IS NULL THEN 1 ELSE 2 END AS stage
  INTO candidate
  FROM rosters r
  JOIN employee_links e ON e.restaurant_id = r.restaurant_id AND e.status = 'approved' AND e.role = 'staff'
  LEFT JOIN day_off_requests d ON d.roster_id = r.id AND d.link_id = e.id
  LEFT JOIN roster_wish_notices first ON first.roster_id = r.id AND first.link_id = e.id AND first.stage = 1
  LEFT JOIN roster_wish_notices follow ON follow.roster_id = r.id AND follow.link_id = e.id AND follow.stage = 2
  WHERE r.published_at IS NULL AND r.start_date > local_time::date
    AND (p_roster = r.id OR (p_roster IS NULL AND (r.collect_wishes_at <= p_now OR first.sent_at IS NOT NULL)))
    AND r.draft->'people' @> jsonb_build_array(jsonb_build_object('link', e.id::text))
    AND NOT COALESCE(d.answered, false)
    AND (first.roster_id IS NULL
         -- Не отправилось (сбой сети, таймаут): повтор через час, всего не больше трёх попыток.
         OR (first.sent_at IS NULL AND first.attempts < 3 AND first.claimed_at + interval '1 hour' <= p_now)
         OR (p_roster IS NULL AND first.sent_at + interval '48 hours' <= p_now AND
             (follow.roster_id IS NULL OR
              (follow.sent_at IS NULL AND follow.attempts < 3 AND follow.claimed_at + interval '1 hour' <= p_now))))
  ORDER BY r.start_date, e.id
  LIMIT 1 FOR UPDATE OF r SKIP LOCKED;
  IF NOT FOUND THEN RETURN NULL; END IF;
  INSERT INTO roster_wish_notices(roster_id, link_id, stage, claimed_at)
    VALUES(candidate.roster_id, candidate.link_id, candidate.stage, p_now)
    ON CONFLICT(roster_id, link_id, stage) DO UPDATE SET claimed_at = EXCLUDED.claimed_at,
      attempts = roster_wish_notices.attempts + 1
    WHERE roster_wish_notices.sent_at IS NULL AND roster_wish_notices.attempts < 3
      AND roster_wish_notices.claimed_at + interval '1 hour' <= p_now;
  IF NOT FOUND THEN RETURN NULL; END IF;
  RETURN to_jsonb(candidate);
END;
$$;
REVOKE ALL ON FUNCTION claim_roster_wish_notice(timestamptz,uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION claim_roster_wish_notice(timestamptz,uuid) TO service_role;

-- 2. Кому уже ушла рассылка: ключ — входящее сообщение Telegram с подтверждением рассылки.
CREATE TABLE broadcast_deliveries (
  update_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  claimed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(update_id, user_id)
);
ALTER TABLE broadcast_deliveries ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON broadcast_deliveries FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, DELETE ON broadcast_deliveries TO service_role;

-- 3. Перенос сотрудника: общие пожелания человека едут с ним. Ответы на графики прежнего ресторана
-- относятся к его периодам и остаются там. Управляющий не переносится туда, где управляющий уже есть.
CREATE OR REPLACE FUNCTION identity_transfer(actor BIGINT, link_id UUID, source_id UUID, target_id UUID)
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
 IF member.role='director' AND EXISTS (SELECT 1 FROM employee_links x WHERE x.restaurant_id=target_id
     AND x.role='director' AND x.status='approved') THEN
  RAISE EXCEPTION 'identity_director_taken';
 END IF;
 UPDATE employee_links SET restaurant_id=target_id,requested_at=now(),
  reviewed_at=now(),reviewed_by=actor WHERE id=link_id RETURNING * INTO member;
 UPDATE weekday_wishes SET restaurant_id=target_id WHERE weekday_wishes.link_id=identity_transfer.link_id;
 UPDATE shift_time_rules SET restaurant_id=target_id WHERE shift_time_rules.link_id=identity_transfer.link_id
  AND shift_time_rules.restaurant_id=source_id;
 RETURN to_jsonb(member);
END $$;
REVOKE ALL ON FUNCTION identity_transfer(BIGINT,UUID,UUID,UUID) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION identity_transfer(BIGINT,UUID,UUID,UUID) TO service_role;

-- 4. Пересечение периодов проверяется под блокировкой ресторана: два менеджера одновременно
-- не создадут графики 16–30 и 20–30.
CREATE FUNCTION rosters_no_overlap() RETURNS trigger LANGUAGE plpgsql SET search_path=public AS $$
BEGIN
 PERFORM 1 FROM restaurants WHERE id=NEW.restaurant_id FOR UPDATE;
 IF EXISTS (SELECT 1 FROM rosters o WHERE o.restaurant_id=NEW.restaurant_id AND o.id<>NEW.id
            AND o.start_date<=NEW.end_date AND o.end_date>=NEW.start_date) THEN
  RAISE EXCEPTION 'roster_overlap' USING ERRCODE='23P01';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION rosters_no_overlap() FROM PUBLIC,anon,authenticated;
CREATE TRIGGER rosters_no_overlap BEFORE INSERT OR UPDATE OF start_date,end_date,restaurant_id ON rosters
 FOR EACH ROW EXECUTE FUNCTION rosters_no_overlap();

NOTIFY pgrst, 'reload schema';
COMMIT;
