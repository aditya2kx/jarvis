-- 018_run_id.sql
-- Adds run_id (uuid4 hex per daily_refresh.main() invocation) to
-- pipeline_runs and source_pulls so the recorder can MERGE-upsert:
--   pipeline_runs  merge_keys = [run_id]          -> 1 row per invocation
--   source_pulls   merge_keys = [run_id, source]  -> 1 row per source per invocation
-- Distinct retry attempts on the same night remain distinct rows by design
-- (that is the attempt history the Pipeline Health tables exist to show).
-- Views recreated to expose run_id (panels do not display it; it links the tables).
-- Applied via: BHAGA_DATASTORE=bigquery python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

ALTER TABLE `jarvis-bhaga-prod.bhaga.pipeline_runs` ADD COLUMN IF NOT EXISTS run_id STRING;
ALTER TABLE `jarvis-bhaga-prod.bhaga.source_pulls`  ADD COLUMN IF NOT EXISTS run_id STRING;

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_pipeline_runs` AS
SELECT run_id, run_date, started_at_utc, finished_at_utc, runtime_s,
       status, failed_step, error
FROM `jarvis-bhaga-prod.bhaga.pipeline_runs`
ORDER BY recorded_at_utc DESC
LIMIT 30;

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_source_pulls` AS
SELECT run_id, run_date, source, started_at_utc, finished_at_utc, status, error
FROM `jarvis-bhaga-prod.bhaga.source_pulls`
ORDER BY started_at_utc DESC
LIMIT 50;
