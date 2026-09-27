-- Monthly personal sales. Apply once in Supabase SQL Editor before deployment.
-- Backend requires SUPABASE_KEY=service_role (never expose this key to clients).
BEGIN;
CREATE TABLE sales_months (
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    month TEXT NOT NULL CHECK (month ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    targets JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(targets) = 'object'),
    glass_price NUMERIC(12,2) NOT NULL DEFAULT 850 CHECK (glass_price > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (user_id, month)
);
CREATE TABLE sales_events (
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    id UUID NOT NULL,
    month TEXT NOT NULL,
    work_date DATE NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('glass','bottle','cocktails','desserts','turnover','postcards','dvd')),
    value NUMERIC(12,2) NOT NULL CHECK (value > 0 AND value <= 100000000),
    voided BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (user_id, id),
    CHECK (month = to_char(work_date, 'YYYY-MM')),
    CHECK (kind NOT IN ('glass','cocktails','postcards','dvd') OR value = trunc(value))
);
CREATE INDEX sales_events_month ON sales_events(user_id, month, created_at, id);
-- Immutable report revisions: correction adds a new row at the same cutoff.
CREATE TABLE sales_reports (
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    id UUID NOT NULL,
    month TEXT NOT NULL,
    cutoff DATE NOT NULL,
    totals JSONB NOT NULL CHECK (jsonb_typeof(totals) = 'object'),
    forecast JSONB NOT NULL,
    records_complete BOOLEAN NOT NULL DEFAULT FALSE,
    source TEXT NOT NULL DEFAULT 'manual_official_report',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (user_id, id),
    CHECK (month = to_char(cutoff, 'YYYY-MM'))
);
CREATE INDEX sales_reports_month ON sales_reports(user_id, month, created_at, id);
ALTER TABLE sales_months ENABLE ROW LEVEL SECURITY;
ALTER TABLE sales_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE sales_reports ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON sales_months, sales_events, sales_reports FROM anon, authenticated;
GRANT ALL ON sales_months, sales_events, sales_reports TO service_role;
COMMIT;
