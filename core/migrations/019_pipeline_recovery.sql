-- 019_pipeline_recovery.sql
-- Adds recovery_retrigger to pipeline_runs so Grafana Pipeline Health can
-- distinguish /bhaga-cloud refresh retriggers that cleared projection markers
-- (BQ data present, scrape skipped, projection re-run) from full scrape runs.
-- Applied via: BHAGA_DATASTORE=bigquery python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

ALTER TABLE `jarvis-bhaga-prod.bhaga.pipeline_runs` ADD COLUMN IF NOT EXISTS recovery_retrigger BOOL;

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_pipeline_runs` AS
SELECT run_id, run_date, started_at_utc, finished_at_utc, runtime_s,
       status, failed_step, error, recovery_retrigger
FROM `jarvis-bhaga-prod.bhaga.pipeline_runs`
ORDER BY recorded_at_utc DESC
LIMIT 30;
