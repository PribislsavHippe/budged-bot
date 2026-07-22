-- =============================================
-- МИГРАЦИЯ v5: Google-токены для синхронизации смен с Google Календарём
-- Выполни в Supabase SQL Editor. Ничего не удаляет.
-- =============================================

ALTER TABLE users ADD COLUMN IF NOT EXISTS google_access_token TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS google_refresh_token TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS google_token_expiry TIMESTAMPTZ;
