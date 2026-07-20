-- =============================================
-- MIGRATION v2: Исправление и улучшения
-- Выполни этот SQL в Supabase SQL Editor
-- =============================================

-- 1. Планируемые доходы/расходы по датам (если таблицы ещё нет)
CREATE TABLE IF NOT EXISTS planned_income (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE NOT NULL,
    amount DECIMAL(12, 2) NOT NULL,
    expected_date DATE NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_planned_income_user_date 
    ON planned_income(user_id, expected_date);

-- 2. Добавляем поле type в planned_income
-- Раньше тип хранился в description как [Доход]/[Расход] — теперь отдельным полем
ALTER TABLE planned_income 
    ADD COLUMN IF NOT EXISTS type TEXT DEFAULT 'income' 
    CHECK (type IN ('income', 'expense'));

-- Мигрируем старые записи: восстанавливаем type из description-префикса
UPDATE planned_income
SET type = 'expense'
WHERE description LIKE '[Расход]%' AND type IS NULL;

UPDATE planned_income
SET type = 'income'
WHERE type IS NULL;

-- 3. Цели накопления (если ещё нет)
CREATE TABLE IF NOT EXISTS goals (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE NOT NULL,
    name TEXT NOT NULL,
    target_amount DECIMAL(12, 2) NOT NULL,
    target_months INT NOT NULL,
    monthly_amount DECIMAL(12, 2) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_goals_user_id ON goals(user_id);

-- 4. Поле salary_days в таблице users (несколько дней через запятую)
ALTER TABLE users ADD COLUMN IF NOT EXISTS salary_days TEXT;

-- 5. Unique constraint в budgets для upsert
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'budgets'
        AND constraint_type = 'UNIQUE'
        AND constraint_name = 'budgets_user_id_category_period_key'
    ) THEN
        ALTER TABLE budgets ADD UNIQUE (user_id, category, period);
    END IF;
END $$;
