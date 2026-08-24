-- 066_payroll_draft_preview_totals.sql
-- Issue #251: ADP Preview URLs are session hashes and 404 in the operator's
-- browser. Store last Preview Total hours + Gross pay instead; /payroll
-- compares those to console Hours and Total pay (wages+tips+bonus+perks).
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c
--   "from core.datastore import ensure_schema; print(ensure_schema())"

ALTER TABLE `jarvis-bhaga-prod.bhaga.payroll_draft_runs`
  ADD COLUMN IF NOT EXISTS preview_hours FLOAT64;

ALTER TABLE `jarvis-bhaga-prod.bhaga.payroll_draft_runs`
  ADD COLUMN IF NOT EXISTS preview_gross FLOAT64;
