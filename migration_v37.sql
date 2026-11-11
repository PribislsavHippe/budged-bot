-- Personal wishes in the shift calendar and two scheduled requests per period.
BEGIN;

ALTER TABLE rosters ADD COLUMN collect_wishes_at timestamptz;
-- Existing periods remain quiet. Only newly created periods start a collection.
ALTER TABLE rosters ALTER COLUMN collect_wishes_at SET DEFAULT now();
ALTER TABLE day_off_requests
  ADD COLUMN roster_id uuid REFERENCES rosters(id) ON DELETE SET NULL,
  ADD COLUMN desired_days jsonb NOT NULL DEFAULT '[]' CHECK (
    jsonb_typeof(desired_days) = 'array' AND jsonb_array_length(desired_days) <= 62
  ),
  ADD COLUMN revision integer NOT NULL DEFAULT 1;
UPDATE day_off_requests d SET roster_id = r.id
FROM rosters r
WHERE d.restaurant_id = r.restaurant_id AND d.start_date = r.start_date AND d.end_date = r.end_date;
CREATE INDEX day_off_requests_roster ON day_off_requests(roster_id);

CREATE TABLE roster_wish_operations (
  id uuid PRIMARY KEY,
  link_id uuid NOT NULL REFERENCES employee_links(id) ON DELETE CASCADE,
  roster_id uuid REFERENCES rosters(id) ON DELETE CASCADE,
  result jsonb NOT NULL
);
CREATE TABLE roster_wish_notices (
  roster_id uuid NOT NULL REFERENCES rosters(id) ON DELETE CASCADE,
  link_id uuid NOT NULL REFERENCES employee_links(id) ON DELETE CASCADE,
  stage smallint NOT NULL CHECK(stage IN (1, 2)),
  claimed_at timestamptz NOT NULL,
  sent_at timestamptz,
  PRIMARY KEY(roster_id, link_id, stage)
);
CREATE INDEX roster_wish_operations_link ON roster_wish_operations(link_id);
CREATE INDEX roster_wish_notices_link ON roster_wish_notices(link_id);
ALTER TABLE roster_wish_operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE roster_wish_notices ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON roster_wish_operations, roster_wish_notices FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON roster_wish_operations TO service_role;
GRANT SELECT, INSERT, UPDATE ON roster_wish_notices TO service_role;

-- Apply date changes in the same transaction as the roster update. A failed
-- move leaves both the old bounds and the employee responses in place.
CREATE FUNCTION carry_roster_wishes() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    UPDATE day_off_requests SET waiting = false WHERE roster_id = OLD.id;
    RETURN OLD;
  END IF;
  IF (NEW.start_date, NEW.end_date) IS NOT DISTINCT FROM (OLD.start_date, OLD.end_date) THEN
    RETURN NEW;
  END IF;
  IF NEW.start_date <> OLD.start_date THEN
    DELETE FROM day_off_requests archived
    USING day_off_requests moving
    WHERE moving.roster_id = OLD.id AND archived.link_id = moving.link_id
      AND archived.start_date = NEW.start_date AND archived.roster_id IS NULL;
  END IF;
  UPDATE day_off_requests d SET
    start_date = NEW.start_date,
    end_date = NEW.end_date,
    days = COALESCE((SELECT jsonb_agg(v ORDER BY v) FROM jsonb_array_elements_text(d.days) v
                     WHERE v BETWEEN NEW.start_date::text AND NEW.end_date::text), '[]'),
    desired_days = COALESCE((SELECT jsonb_agg(v ORDER BY v) FROM jsonb_array_elements_text(d.desired_days) v
                             WHERE v BETWEEN NEW.start_date::text AND NEW.end_date::text), '[]'),
    revision = revision + 1
  WHERE d.roster_id = OLD.id;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION carry_roster_wishes() FROM PUBLIC, anon, authenticated;
CREATE TRIGGER carry_roster_wishes AFTER UPDATE OF start_date, end_date ON rosters
FOR EACH ROW EXECUTE FUNCTION carry_roster_wishes();
CREATE TRIGGER close_roster_wishes BEFORE DELETE ON rosters
FOR EACH ROW EXECUTE FUNCTION carry_roster_wishes();

CREATE FUNCTION write_roster_wishes(
  p_actor bigint, p_link uuid, p_roster uuid, p_operation uuid, p_revision integer,
  p_changes jsonb, p_weekdays jsonb, p_pattern text, p_clear boolean, p_today date
) RETURNS jsonb LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE
  employee employee_links%ROWTYPE;
  period rosters%ROWTYPE;
  answer day_off_requests%ROWTYPE;
  permanent weekday_wishes%ROWTYPE;
  result jsonb;
  choices jsonb := '{}';
  required_dates jsonb;
  preferred_dates jsonb;
BEGIN
  IF p_roster IS NOT NULL THEN
    SELECT * INTO period FROM rosters WHERE id = p_roster FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('status', 'period'); END IF;
  END IF;
  SELECT * INTO employee FROM employee_links
  WHERE id = p_link AND user_id = p_actor AND status = 'approved' AND role = 'staff' FOR UPDATE;
  IF NOT FOUND THEN RETURN jsonb_build_object('status', 'access'); END IF;
  IF p_roster IS NOT NULL AND (period.restaurant_id <> employee.restaurant_id OR NOT
      COALESCE(period.draft->'people' @> jsonb_build_array(jsonb_build_object('link', p_link::text)), false)) THEN
    RETURN jsonb_build_object('status', 'access');
  END IF;
  SELECT o.result INTO result FROM roster_wish_operations o WHERE o.id = p_operation AND o.link_id = p_link;
  IF FOUND THEN RETURN result; END IF;

  IF jsonb_typeof(p_changes) IS DISTINCT FROM 'object' OR p_today IS NULL OR
     (p_pattern IS NOT NULL AND p_pattern !~ '^[1-7]/[1-7]$') THEN
    RETURN jsonb_build_object('status', 'invalid');
  END IF;
  IF p_weekdays IS NOT NULL THEN
    IF jsonb_typeof(p_weekdays) <> 'array' THEN RETURN jsonb_build_object('status', 'invalid'); END IF;
    IF jsonb_array_length(p_weekdays) > 7 OR EXISTS (
      SELECT 1 FROM jsonb_array_elements(p_weekdays) v
      WHERE jsonb_typeof(v) <> 'number' OR v::text !~ '^[0-6]$'
    ) THEN RETURN jsonb_build_object('status', 'invalid'); END IF;
  END IF;
  IF p_roster IS NULL AND (p_changes <> '{}' OR p_clear) THEN
    RETURN jsonb_build_object('status', 'period');
  END IF;

  IF p_roster IS NOT NULL THEN
    IF period.end_date < p_today OR EXISTS (
      SELECT 1 FROM jsonb_each_text(p_changes) c
      WHERE c.key < greatest(period.start_date, p_today)::text OR c.key > period.end_date::text
         OR c.key !~ '^\d{4}-\d{2}-\d{2}$' OR c.value IS NULL OR c.value NOT IN ('required','preferred','none')
    ) THEN RETURN jsonb_build_object('status', 'period'); END IF;
    -- Cast only after validating the canonical shape.
    PERFORM c.key::date FROM jsonb_each_text(p_changes) c;
    SELECT * INTO answer FROM day_off_requests WHERE link_id = p_link AND roster_id = p_roster FOR UPDATE;
    IF p_revision IS NOT NULL AND p_revision <> COALESCE(answer.revision, 0) THEN
      RETURN jsonb_build_object('status', 'conflict');
    END IF;
    SELECT COALESCE(jsonb_object_agg(day, kind), '{}') INTO choices
    FROM (
      SELECT v AS day, 'required' AS kind FROM jsonb_array_elements_text(COALESCE(answer.days, '[]')) v
      UNION ALL
      SELECT v, 'preferred' FROM jsonb_array_elements_text(COALESCE(answer.desired_days, '[]')) v
    ) prior
    WHERE NOT p_clear OR day < p_today::text;
    SELECT COALESCE(jsonb_agg(key ORDER BY key) FILTER (WHERE value = 'required'), '[]'),
           COALESCE(jsonb_agg(key ORDER BY key) FILTER (WHERE value = 'preferred'), '[]')
    INTO required_dates, preferred_dates FROM jsonb_each_text(choices || p_changes);
    IF jsonb_array_length(required_dates) > 12 THEN RETURN jsonb_build_object('status', 'limit'); END IF;

    INSERT INTO day_off_requests AS d (link_id, restaurant_id, roster_id, start_date, end_date,
        days, desired_days, waiting, answered, revision)
    VALUES (p_link, employee.restaurant_id, p_roster, period.start_date, period.end_date,
        required_dates, preferred_dates, false, true, 1)
    ON CONFLICT (link_id, start_date) DO UPDATE SET roster_id = excluded.roster_id,
      restaurant_id = excluded.restaurant_id,
      end_date = excluded.end_date, days = excluded.days, desired_days = excluded.desired_days,
      waiting = false, answered = true, revision = d.revision + 1, updated_at = now()
    RETURNING * INTO answer;
  END IF;

  SELECT * INTO permanent FROM weekday_wishes WHERE link_id = p_link AND restaurant_id = employee.restaurant_id;
  IF COALESCE(jsonb_array_length(p_weekdays), 0) > 0 OR p_pattern IS NOT NULL THEN
    INSERT INTO weekday_wishes AS w (link_id, restaurant_id, days, pattern)
    VALUES (p_link, employee.restaurant_id, COALESCE(p_weekdays, '[]'), p_pattern)
    ON CONFLICT (link_id) DO UPDATE SET restaurant_id = excluded.restaurant_id,
      days = CASE WHEN w.restaurant_id = excluded.restaurant_id THEN
        (SELECT COALESCE(jsonb_agg(DISTINCT v ORDER BY v), '[]')
         FROM jsonb_array_elements(w.days || excluded.days) v) ELSE excluded.days END,
      pattern = COALESCE(excluded.pattern, CASE WHEN w.restaurant_id = excluded.restaurant_id THEN w.pattern END),
      updated_at = now()
    RETURNING * INTO permanent;
  END IF;
  result := jsonb_build_object('status','ok','revision',COALESCE(answer.revision,0),
    'days',COALESCE(answer.days,'[]'),'desired_days',COALESCE(answer.desired_days,'[]'),
    'weekdays',COALESCE(permanent.days,'[]'),'pattern',permanent.pattern);
  INSERT INTO roster_wish_operations(id, link_id, roster_id, result) VALUES(p_operation, p_link, p_roster, result);
  RETURN result;
END;
$$;

-- Reserve one delivery before sending. An uncertain Telegram response is not
-- retried automatically, so it cannot turn into a duplicate reminder.
CREATE FUNCTION claim_roster_wish_notice(p_now timestamptz, p_roster uuid)
RETURNS jsonb LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE
  local_time timestamp := p_now AT TIME ZONE 'Europe/Moscow';
  candidate record;
BEGIN
  IF local_time::time < time '10:00' OR local_time::time >= time '20:00' THEN RETURN NULL; END IF;
  SELECT r.id AS roster_id, r.start_date, r.end_date, e.id AS link_id, e.user_id,
         CASE WHEN first.roster_id IS NULL THEN 1 ELSE 2 END AS stage
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
    AND (first.roster_id IS NULL OR
         (p_roster IS NULL AND first.sent_at + interval '48 hours' <= p_now AND follow.roster_id IS NULL))
  ORDER BY r.start_date, e.id
  LIMIT 1 FOR UPDATE OF r SKIP LOCKED;
  IF NOT FOUND THEN RETURN NULL; END IF;
  INSERT INTO roster_wish_notices(roster_id, link_id, stage, claimed_at)
    VALUES(candidate.roster_id, candidate.link_id, candidate.stage, p_now) ON CONFLICT DO NOTHING;
  IF NOT FOUND THEN RETURN NULL; END IF;
  RETURN to_jsonb(candidate);
END;
$$;

REVOKE ALL ON FUNCTION write_roster_wishes(bigint,uuid,uuid,uuid,integer,jsonb,jsonb,text,boolean,date)
  FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION claim_roster_wish_notice(timestamptz,uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION write_roster_wishes(bigint,uuid,uuid,uuid,integer,jsonb,jsonb,text,boolean,date) TO service_role;
GRANT EXECUTE ON FUNCTION claim_roster_wish_notice(timestamptz,uuid) TO service_role;
NOTIFY pgrst, 'reload schema';
COMMIT;
