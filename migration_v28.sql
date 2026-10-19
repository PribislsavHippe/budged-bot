-- Automatic report import: one-time image consent and persistent review.
-- Apply after v27. Does not grant employees category configuration access.
BEGIN;
ALTER TABLE users ADD COLUMN image_recognition_consent_at TIMESTAMPTZ;
ALTER TABLE category_reports ADD COLUMN review_status TEXT NOT NULL DEFAULT 'confirmed'
 CHECK(review_status IN ('confirmed','flagged'));
CREATE FUNCTION review_category_report(actor BIGINT, operation UUID, outcome TEXT)
RETURNS TEXT LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE result TEXT;
BEGIN
 IF outcome NOT IN ('confirmed','flagged') OR outcome IS NULL THEN
   RAISE EXCEPTION 'category_invalid'; END IF;
 -- Flagging is irreversible for this operation: a delayed confirmation must not
 -- restore disputed data. A corrected report is a separate operation.
 UPDATE category_reports SET review_status=CASE WHEN review_status='flagged' THEN 'flagged' ELSE outcome END
 WHERE id=operation AND user_id=actor RETURNING review_status INTO result;
 IF NOT FOUND THEN RAISE EXCEPTION 'category_forbidden'; END IF;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION review_category_report(BIGINT,UUID,TEXT) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION review_category_report(BIGINT,UUID,TEXT) TO service_role;
NOTIFY pgrst,'reload schema';
COMMIT;
