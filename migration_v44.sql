-- Удаляет старую функцию включения приватного режима (v15). Apply after v43.
-- Она стирала личный журнал без резервных копий; с v20 код использует activate_private_money_with_backups,
-- а затем перенос с копиями из v24. Старую никто не вызывает, но она оставалась доступной серверу (ревью 15.11.2026).
BEGIN;
DROP FUNCTION IF EXISTS activate_private_money(BIGINT,BIGINT[],JSONB);
NOTIFY pgrst, 'reload schema';
COMMIT;
