-- =============================================
-- МИГРАЦИЯ v4: расписание смен + поля для Яндекс-Календаря
-- Выполни в Supabase SQL Editor. Ничего не удаляет.
-- =============================================

-- Запланированные смены. День считается сменой, даже если чая ещё нет —
-- по этим дням бот вечером спрашивает про чаевые.
CREATE TABLE IF NOT EXISTS shifts (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE NOT NULL,
    shift_date DATE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, shift_date)
);
CREATE INDEX IF NOT EXISTS idx_shifts_user ON shifts(user_id, shift_date);

-- Supabase включает RLS по умолчанию — выключаем (ключ только на сервере).
ALTER TABLE shifts DISABLE ROW LEVEL SECURITY;

-- Для синхронизации с Яндекс-Календарём (CalDAV) — заполнится в фазе 2.
ALTER TABLE users ADD COLUMN IF NOT EXISTS yandex_email TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS yandex_app_password TEXT;
