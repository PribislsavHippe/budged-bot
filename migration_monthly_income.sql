-- =============================================
-- МИГРАЦИЯ: Добавить поле monthly_income в таблицу users
-- Нужно для режима новичка (работа без истории транзакций)
-- Выполни в Supabase → SQL Editor
-- =============================================

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS monthly_income DECIMAL(12, 2) DEFAULT NULL;

-- Готово. Возвращает: "Success. No rows returned."
