-- 097_adp_hire_dates.sql
-- ADP hire date per person, read by the nightly pay_info pass (Directory column or the
-- employee profile). The Operator Console's shift draft keeps anyone hired in the
-- new-hire window beside experienced staff until their supervised openers / closers
-- are done (Issue #450). One row per (store, employee_name), MERGEd — re-reads only
-- refresh it. Backfill: BHAGA_ADP_HIRE_DATES=1 on bhaga-daily-refresh.
-- Apply: BHAGA_DATASTORE=bigquery python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.adp_hire_dates` (
  store           STRING    NOT NULL,
  employee_name   STRING    NOT NULL,
  hire_date       DATE      NOT NULL,
  source          STRING    NOT NULL,  -- 'directory' | 'profile'
  scraped_at_utc  TIMESTAMP NOT NULL
);
