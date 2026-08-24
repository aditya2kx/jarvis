-- 065_payroll_draft_runs.sql
-- Issue #251: last ADP Start→Preview run per pay period (console + Monday
-- 07:00 scheduler). Stores the ADP worksheet URL so the operator can open
-- Preview in their own browser. Console-only — not a Grafana freshness target.
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.payroll_draft_runs` (
  store            STRING    NOT NULL,
  period_start     DATE      NOT NULL,
  period_end       DATE      NOT NULL,
  status           STRING    NOT NULL,  -- running | ok | fail
  preview_url      STRING,
  error            STRING,
  started_at_utc   TIMESTAMP,
  finished_at_utc  TIMESTAMP
);
