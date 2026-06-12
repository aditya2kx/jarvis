-- 017_source_pulls.sql
-- Pipeline Health v2 (dashboard v38): two-table design.
-- 1. source_pulls: one appended row per per-source pull ATTEMPT (square | adp |
--    google_reviews) per daily_refresh run. Written best-effort by
--    daily_refresh._record_pipeline_run() alongside the pipeline_runs row.
--    Skipped sources (marker already done / --skip flags) never enter the
--    phase-1 results dict, so only real attempts are recorded.
-- 2. vw_pipeline_runs: last 30 run outcomes for the "Pipeline Runs" table panel.
-- 3. vw_source_pulls: last 50 pull attempts for the "Data Source Pulls" panel.
--    NOTE: square_transactions.scraped_at_utc / adp_shifts.scraped_at_utc are
--    all NULL in prod (verified 2026-06-12), so there is NO derived-history
--    union — the view is empty until the first nightly run records pulls.
-- 4. Drops vw_pipeline_health (016) — replaced by the two views above; its six
--    stat panels are removed from the dashboard in the same deploy.
-- Applied via: BHAGA_DATASTORE=bigquery python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.source_pulls` (
  run_date         DATE,
  store            STRING,
  source           STRING,    -- square | adp | google_reviews
  started_at_utc   TIMESTAMP,
  finished_at_utc  TIMESTAMP,
  status           STRING,    -- success | failed
  error            STRING,
  recorded_at_utc  TIMESTAMP
);

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_pipeline_runs` AS
SELECT run_date, started_at_utc, finished_at_utc, runtime_s,
       status, failed_step, error
FROM `jarvis-bhaga-prod.bhaga.pipeline_runs`
ORDER BY recorded_at_utc DESC
LIMIT 30;

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_source_pulls` AS
SELECT run_date, source, started_at_utc, finished_at_utc, status, error
FROM `jarvis-bhaga-prod.bhaga.source_pulls`
ORDER BY started_at_utc DESC
LIMIT 50;

DROP VIEW IF EXISTS `jarvis-bhaga-prod.bhaga.vw_pipeline_health`;
