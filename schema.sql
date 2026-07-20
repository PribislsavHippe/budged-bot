-- =============================================
-- BUDGET BOT v2 — чистая схема
-- Выполни в Supabase SQL Editor.
-- Если остались старые таблицы — сначала удали их:
--   DROP TABLE IF EXISTS transactions, scheduled_payments, budgets,
--     google_tokens, planned_income, goals, users CASCADE;
-- =============================================

CREATE TABLE users (
    id BIGINT PRIMARY KEY,              -- Telegram user_id
    username TEXT,
    first_name TEXT,
    onboarded BOOLEAN DEFAULT FALSE,    -- прошёл ли стартовую сверку балансов
    shift_goal NUMERIC(12, 2),          -- план смены (цель по чаю)
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Журнал операций — ЕДИНСТВЕННЫЙ источник правды о деньгах.
-- Баланс счёта = сумма signed_amount по этому счёту. Ничего не хранится отдельно.
CREATE TABLE entries (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE NOT NULL,
    kind TEXT CHECK (kind IN ('income', 'expense', 'adjustment')) NOT NULL,
    account TEXT CHECK (account IN ('cash', 'card')) NOT NULL,
    -- Знаковая сумма: доход +, расход -, сверка ± (разница между реальным и расчётным).
    signed_amount NUMERIC(12, 2) NOT NULL,
    category TEXT NOT NULL DEFAULT 'Прочее',
    note TEXT,                          -- исходный текст пользователя / банка
    order_amount NUMERIC(12, 2),        -- чек заказа (из банковского уведомления)
    tip_percent NUMERIC(5, 1),          -- процент чаевых от чека
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_entries_user ON entries(user_id, created_at DESC);
CREATE INDEX idx_entries_user_account ON entries(user_id, account);

-- Supabase в новых проектах включает Row Level Security по умолчанию,
-- и анонимный ключ не может писать в таблицы. Ключ хранится только на
-- сервере бота, наружу не отдаётся — поэтому RLS выключаем.
ALTER TABLE users DISABLE ROW LEVEL SECURITY;
ALTER TABLE entries DISABLE ROW LEVEL SECURITY;
