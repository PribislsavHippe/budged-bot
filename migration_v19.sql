-- Personal wage accruals: service charge is not received cash or card income.
-- Apply in Supabase before deploying code that writes kind='accrual'.
BEGIN;
ALTER TABLE entries DROP CONSTRAINT IF EXISTS entries_kind_check;
ALTER TABLE entries ADD CONSTRAINT entries_kind_check
  CHECK (kind IN ('income', 'expense', 'adjustment', 'accrual'));
ALTER TABLE entries DROP CONSTRAINT IF EXISTS entries_account_check;
ALTER TABLE entries ADD CONSTRAINT entries_account_check
  CHECK (account IN ('cash', 'card', 'pending'));
ALTER TABLE entries DROP CONSTRAINT IF EXISTS entries_accrual_account_check;
ALTER TABLE entries ADD CONSTRAINT entries_accrual_account_check
  CHECK ((kind = 'accrual' AND account = 'pending') OR (kind <> 'accrual' AND account <> 'pending'));
NOTIFY pgrst, 'reload schema';
COMMIT;
