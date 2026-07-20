-- =============================================
-- МИГРАЦИЯ НА v2 ДЛЯ СУЩЕСТВУЮЩЕЙ БАЗЫ
-- Выполни в Supabase SQL Editor. Ничего не удаляет:
-- старые таблицы остаются как архив, бот их больше не использует.
-- =============================================

-- Новый флаг: прошёл ли пользователь стартовую сверку балансов v2.
-- У всех существующих пользователей будет FALSE — при первом сообщении
-- бот попросит указать реальные остатки и начнёт учёт с чистой точки.
ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarded BOOLEAN DEFAULT FALSE;

-- Журнал операций — единственный источник правды о деньгах в v2.
CREATE TABLE IF NOT EXISTS entries (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE NOT NULL,
    kind TEXT CHECK (kind IN ('income', 'expense', 'adjustment')) NOT NULL,
    account TEXT CHECK (account IN ('cash', 'card')) NOT NULL,
    -- Знаковая сумма: доход +, расход -, сверка ± (разница факта и журнала).
    signed_amount NUMERIC(12, 2) NOT NULL,
    category TEXT NOT NULL DEFAULT 'Прочее',
    note TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_entries_user ON entries(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_entries_user_account ON entries(user_id, account);

-- Старые таблицы (transactions, budgets, scheduled_payments, goals,
-- planned_income, google_tokens) НЕ трогаем — это твой архив.
-- Когда убедишься, что v2 работает, их можно удалить отдельно:
--   DROP TABLE IF EXISTS transactions, scheduled_payments, budgets,
--     google_tokens, planned_income, goals CASCADE;
