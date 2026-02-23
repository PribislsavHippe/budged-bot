-- ============================================================
-- MIGRATION v4: История изменений бюджетов + Прогнозируемый доход
-- Выполни в Supabase → SQL Editor
-- ============================================================

-- 1. История изменений бюджетов (для анализа прогресса)
CREATE TABLE IF NOT EXISTS budget_history (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT REFERENCES users(id) ON DELETE CASCADE NOT NULL,
    category    TEXT NOT NULL,
    old_amount  DECIMAL(12, 2),
    new_amount  DECIMAL(12, 2) NOT NULL,
    changed_at  TIMESTAMPTZ DEFAULT NOW(),
    reason      TEXT  -- 'onboarding', 'manual', 'auto_adjust'
);

CREATE INDEX IF NOT EXISTS idx_budget_history_user
    ON budget_history(user_id, changed_at DESC);

-- 2. Прогнозируемый доход — отдельный учёт (выставляется вручную на шаге 1)
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS predicted_income DECIMAL(12, 2),
    ADD COLUMN IF NOT EXISTS average_income   DECIMAL(12, 2);

-- 3. Дни зарплаты уже есть (salary_days TEXT), но добавим индекс на users
CREATE INDEX IF NOT EXISTS idx_users_active ON users(is_active) WHERE is_active = TRUE;

-- ============================================================
-- Триггер: автоматически пишем в budget_history при изменении budgets
-- ============================================================
CREATE OR REPLACE FUNCTION trg_budget_history()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO budget_history(user_id, category, old_amount, new_amount, reason)
        VALUES (NEW.user_id, NEW.category, NULL, NEW.limit_amount, 'created');
    ELSIF TG_OP = 'UPDATE' AND OLD.limit_amount IS DISTINCT FROM NEW.limit_amount THEN
        INSERT INTO budget_history(user_id, category, old_amount, new_amount, reason)
        VALUES (NEW.user_id, NEW.category, OLD.limit_amount, NEW.limit_amount, 'updated');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_budgets_history ON budgets;
CREATE TRIGGER trg_budgets_history
    AFTER INSERT OR UPDATE ON budgets
    FOR EACH ROW EXECUTE FUNCTION trg_budget_history();

-- ============================================================
-- Проверка после выполнения:
-- SELECT * FROM budget_history LIMIT 10;
-- SELECT predicted_income, average_income FROM users LIMIT 5;
-- ============================================================
