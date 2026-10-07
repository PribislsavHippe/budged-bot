-- Mark the shorter first-use guidance as a new onboarding cohort.
-- Apply after v22, before deploying the updated bot.
BEGIN;
ALTER TABLE research_subjects ALTER COLUMN onboarding_version SET DEFAULT 3;
COMMIT;
