-- =============================================
-- СХЕМА БАЗЫ ДАННЫХ ДЛЯ BUDGET BOT
-- Выполни этот SQL в Supabase SQL Editor
-- =============================================

-- Пользователи
CREATE TABLE users (
    id BIGINT PRIMARY KEY,  -- Telegram user_id
    username TEXT,
    first_name TEXT,
    timezone TEXT DEFAULT 'Europe/Moscow',
    salary_day INT DEFAULT 1,         -- День получения зарплаты (1-31)
    salary_day_2 INT,                  -- Второй день (аванс), если есть
    expense_reminder_hour INT DEFAULT 21, -- В какой час напоминать о расходах
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Транзакции (доходы и расходы)
CREATE TABLE transactions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    type TEXT CHECK (type IN ('income', 'expense')) NOT NULL,
    amount DECIMAL(12, 2) NOT NULL,
    category TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Обязательные платежи (аренда, подписки и т.д.)
CREATE TABLE scheduled_payments (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,               -- Название: "Аренда", "Netflix"
    amount DECIMAL(12, 2) NOT NULL,
    day_of_month INT NOT NULL,        -- День месяца для оплаты
    category TEXT DEFAULT 'Обязательные',
    remind_days_before INT DEFAULT 2, -- За сколько дней напомнить
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Бюджеты по категориям
CREATE TABLE budgets (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    limit_amount DECIMAL(12, 2) NOT NULL,
    period TEXT CHECK (period IN ('weekly', 'monthly')) DEFAULT 'monthly',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, category, period)
);

-- Google Calendar токены
CREATE TABLE google_tokens (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    access_token TEXT,
    refresh_token TEXT,
    token_expiry TIMESTAMPTZ,
    calendar_id TEXT DEFAULT 'primary',
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Индексы для быстрых запросов
CREATE INDEX idx_transactions_user_id ON transactions(user_id);
CREATE INDEX idx_transactions_created_at ON transactions(created_at);
CREATE INDEX idx_scheduled_payments_user_id ON scheduled_payments(user_id);
CREATE INDEX idx_scheduled_payments_day ON scheduled_payments(day_of_month);

-- =============================================
-- КАТЕГОРИИ ПО УМОЛЧАНИЮ (для подсказок)
-- =============================================
-- Расходы: Еда, Транспорт, Жильё, Развлечения, Здоровье,
--          Одежда, Связь, Образование, Обязательные, Прочее
-- Доходы:  Зарплата, Аванс, Фриланс, Подарок, Прочее
