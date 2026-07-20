-- ============================================================
-- MIGRATION v3: Текущий баланс + Gemini rate limit в Supabase
-- Выполни в Supabase → SQL Editor
-- ============================================================

-- 1. Дата последнего вызова Gemini (вместо in-memory переменной)
--    При рестарте Render счётчик больше не сбрасывается
ALTER TABLE users ADD COLUMN IF NOT EXISTS gemini_last_analysis_date DATE;

-- 2. Текущий баланс — пользователь указывает при онбординге
ALTER TABLE users ADD COLUMN IF NOT EXISTS current_balance DECIMAL(12, 2);

-- 3. Ежемесячный доход (уже должен быть из v2, но на всякий случай)
ALTER TABLE users ADD COLUMN IF NOT EXISTS monthly_income DECIMAL(12, 2);

-- 4. Проверяем что salary_days есть (из v2)
ALTER TABLE users ADD COLUMN IF NOT EXISTS salary_days TEXT;

-- 5. Индекс на planned_income по дате (ускоряет прогнозы)
CREATE INDEX IF NOT EXISTS idx_planned_income_user_date
    ON planned_income(user_id, expected_date);

-- 6. Индекс на transactions по дате (ускоряет monthly stats)
CREATE INDEX IF NOT EXISTS idx_transactions_user_created
    ON transactions(user_id, created_at DESC);

-- 7. Индекс на scheduled_payments
CREATE INDEX IF NOT EXISTS idx_scheduled_payments_user_active
    ON scheduled_payments(user_id, is_active);

-- ============================================================
-- Проверка после выполнения:
-- ============================================================
-- SELECT column_name, data_type FROM information_schema.columns
-- WHERE table_name = 'users' ORDER BY ordinal_position;
