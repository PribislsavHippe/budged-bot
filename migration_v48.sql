-- Menu hints: dish and drink cards read from the PDF files the kitchen and bar keep on Google Drive.
-- One import per uploaded file. A new upload of the same file name replaces the old one, which stays for 7 days
-- and can be brought back. Apply after v47.
BEGIN;
CREATE TABLE menu_imports (
  id uuid PRIMARY KEY,
  -- File name without extension: "Основное меню", "Коктейли ОСЕНЬ-ЗИМА 2026_27".
  source text NOT NULL CHECK (char_length(source) BETWEEN 1 AND 120),
  -- Telegram file_unique_id: the same file sent twice is imported once.
  file_key text NOT NULL UNIQUE CHECK (char_length(file_key) BETWEEN 1 AND 120),
  created_by bigint NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  item_count integer NOT NULL DEFAULT 0 CHECK (item_count BETWEEN 0 AND 500),
  -- Not yet shown (still loading) until activated; replaced_at is set when a newer upload takes its place.
  activated_at timestamptz,
  replaced_at timestamptz
);
CREATE UNIQUE INDEX menu_imports_one_active ON menu_imports (lower(source))
  WHERE activated_at IS NOT NULL AND replaced_at IS NULL;
CREATE TABLE menu_photos (
  id uuid PRIMARY KEY,
  import_id uuid NOT NULL REFERENCES menu_imports(id) ON DELETE CASCADE,
  -- JPEG up to 1000 px, base64.
  data text NOT NULL CHECK (octet_length(data) <= 600000),
  -- Telegram file_id after the first send, so the photo is uploaded once.
  file_id text CHECK (char_length(file_id) <= 200)
);
CREATE TABLE menu_items (
  id uuid PRIMARY KEY,
  import_id uuid NOT NULL REFERENCES menu_imports(id) ON DELETE CASCADE,
  position integer NOT NULL CHECK (position BETWEEN 0 AND 499),
  -- Name, section, weight, composition, allergens, serving, description, page — as written in the file.
  card jsonb NOT NULL CHECK (jsonb_typeof(card) = 'object' AND octet_length(card::text) <= 30000),
  photo_id uuid REFERENCES menu_photos(id) ON DELETE SET NULL,
  UNIQUE (import_id, position)
);
CREATE INDEX menu_items_import ON menu_items (import_id);
CREATE INDEX menu_photos_import ON menu_photos (import_id);

-- Show a finished import: the previous active import of the same file name is replaced (kept for 7 days).
-- Also used to bring back a replaced import. Imports replaced more than 7 days ago are deleted here.
CREATE FUNCTION activate_menu_import(p_import uuid) RETURNS text
LANGUAGE plpgsql SECURITY INVOKER SET search_path = public AS $$
DECLARE
  target menu_imports;
BEGIN
  SELECT * INTO target FROM menu_imports WHERE id = p_import FOR UPDATE;
  IF NOT FOUND THEN
    RETURN 'missing';
  END IF;
  PERFORM 1 FROM menu_imports WHERE lower(source) = lower(target.source) FOR UPDATE;
  IF target.activated_at IS NOT NULL AND target.replaced_at IS NULL THEN
    RETURN 'active';
  END IF;
  UPDATE menu_imports SET replaced_at = now()
   WHERE lower(source) = lower(target.source) AND id <> target.id
     AND activated_at IS NOT NULL AND replaced_at IS NULL;
  UPDATE menu_imports SET activated_at = coalesce(activated_at, now()), replaced_at = NULL WHERE id = target.id;
  DELETE FROM menu_imports WHERE replaced_at < now() - interval '7 days';
  -- Loads that broke off halfway and were never shown.
  DELETE FROM menu_imports WHERE activated_at IS NULL AND created_at < now() - interval '1 day';
  RETURN 'ok';
END $$;

ALTER TABLE menu_imports ENABLE ROW LEVEL SECURITY;
ALTER TABLE menu_photos ENABLE ROW LEVEL SECURITY;
ALTER TABLE menu_items ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON menu_imports, menu_photos, menu_items FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON menu_imports, menu_photos, menu_items TO service_role;
REVOKE ALL ON FUNCTION activate_menu_import(uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION activate_menu_import(uuid) TO service_role;
NOTIFY pgrst, 'reload schema';
COMMIT;
