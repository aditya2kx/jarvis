-- 086_payroll_draft_packet_totals.sql
-- Issue #358: the console totals each ok draft keyed from (hours, gross with the
-- solo premium). When a later hours load moves them, the In Progress ADP draft
-- is out of date and the nightly / Sync ADP re-runs it (resume only, never a
-- new payroll).
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c
--   "from core.datastore import ensure_schema; print(ensure_schema())"

ALTER TABLE `jarvis-bhaga-prod.bhaga.payroll_draft_runs`
  ADD COLUMN IF NOT EXISTS packet_hours FLOAT64;

ALTER TABLE `jarvis-bhaga-prod.bhaga.payroll_draft_runs`
  ADD COLUMN IF NOT EXISTS packet_pay FLOAT64;
