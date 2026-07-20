-- =============================================
-- МИГРАЦИЯ v3: данные для статистики и мини-апа
-- Выполни в Supabase SQL Editor. Ничего не удаляет.
-- =============================================

-- Из банковского уведомления теперь сохраняем не только сумму чаевых,
-- но и чек заказа с процентом — топливо для статистики.
ALTER TABLE entries ADD COLUMN IF NOT EXISTS order_amount NUMERIC(12, 2);
ALTER TABLE entries ADD COLUMN IF NOT EXISTS tip_percent NUMERIC(5, 1);

-- План смены (цель по чаю), меняется в любой момент командой «план 2500»
ALTER TABLE users ADD COLUMN IF NOT EXISTS shift_goal NUMERIC(12, 2);
