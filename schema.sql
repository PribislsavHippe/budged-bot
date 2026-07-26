-- =============================================
-- BUDGET BOT v2 — чистая схема
-- Выполни в Supabase SQL Editor.
-- Если остались старые таблицы — сначала удали их:
--   DROP TABLE IF EXISTS transactions, scheduled_payments, budgets,
--     google_tokens, planned_income, goals, users CASCADE;
-- =============================================

-- Профиль. Намеренно без имени и @username: бот берёт их из самого сообщения
-- Telegram, когда нужно поздороваться, и не хранит. В базе человек — только
-- числовой id, чтобы её нельзя было читать как ведомость с фамилиями.
CREATE TABLE users (
    id BIGINT PRIMARY KEY,              -- Telegram user_id
    onboarded BOOLEAN DEFAULT FALSE,    -- прошёл ли знакомство
    shift_goal NUMERIC(12, 2),          -- план смены (цель по чаю)
    google_access_token TEXT,           -- Google Календарь (OAuth)
    google_refresh_token TEXT,
    google_token_expiry TIMESTAMPTZ,
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

-- Запланированные смены (по этим дням бот вечером спрашивает про чай).
CREATE TABLE shifts (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE NOT NULL,
    shift_date DATE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, shift_date)
);
CREATE INDEX idx_shifts_user ON shifts(user_id, shift_date);

-- Supabase в новых проектах включает Row Level Security по умолчанию,
-- и анонимный ключ не может писать в таблицы. Ключ хранится только на
-- сервере бота, наружу не отдаётся — поэтому RLS выключаем.
ALTER TABLE users DISABLE ROW LEVEL SECURITY;
ALTER TABLE entries DISABLE ROW LEVEL SECURITY;
ALTER TABLE shifts DISABLE ROW LEVEL SECURITY;
