-- Days off are now typed as text. The bot must remember whom it is waiting for. Apply after v32.
-- answered: the person has sent days (or "none"); waiting: the bot expects the next numbers from them.
BEGIN;
ALTER TABLE day_off_requests ADD COLUMN answered BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE day_off_requests ADD COLUMN waiting BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX day_off_requests_waiting ON day_off_requests(link_id,end_date) WHERE waiting;
COMMIT;
