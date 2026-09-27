-- Persistent calendar delivery status. Apply after v7, before deploying.
BEGIN;
ALTER TABLE users ADD COLUMN IF NOT EXISTS google_reconnect_required BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE shifts ADD COLUMN IF NOT EXISTS google_synced BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE shifts ADD COLUMN IF NOT EXISTS google_sync_error TEXT;
CREATE INDEX IF NOT EXISTS idx_shifts_google_pending ON shifts(shift_date) WHERE NOT google_synced;
COMMIT;
