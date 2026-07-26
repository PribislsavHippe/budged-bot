-- =============================================
-- МИГРАЦИЯ v6: убираем персональные данные
-- Выполни в Supabase SQL Editor.
--
-- После неё в базе не остаётся ничего, по чему человека можно узнать
-- глазами: только числовой Telegram id, суммы и даты. Имя и @username
-- боту не нужны — Telegram присылает их в каждом сообщении, и для
-- приветствия бот берёт их оттуда, а не из базы.
-- =============================================

ALTER TABLE users DROP COLUMN IF EXISTS username;
ALTER TABLE users DROP COLUMN IF EXISTS first_name;

-- Заодно поля под Яндекс-Календарь из миграции v4: та ветка не состоялась
-- (сделали Google), колонки всегда пустые. Пустая колонка с названием
-- «app_password» в открытой схеме пугает людей на ровном месте.
ALTER TABLE users DROP COLUMN IF EXISTS yandex_email;
ALTER TABLE users DROP COLUMN IF EXISTS yandex_app_password;
