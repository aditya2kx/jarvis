-- 016_pipeline_runs.sql
-- 1. pipeline_runs: one appended row per daily_refresh terminal outcome
--    (success | failed | halted | otp_pending). Written best-effort by
--    daily_refresh.main(); latest row per recorded_at_utc wins.
-- 2. vw_pipeline_health: single-row view for the Grafana "0. Pipeline Health"
--    section — latest run outcome + per-source last successful pull from the
--    raw tables' scrape timestamps.
-- Applied via: python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.pipeline_runs` (
  run_date         DATE,
  store            STRING,
  started_at_utc   TIMESTAMP,
  finished_at_utc  TIMESTAMP,
  runtime_s        FLOAT64,
  status           STRING,
  failed_step      STRING,
  error            STRING,
  exit_code        INT64,
  recorded_at_utc  TIMESTAMP
);

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_pipeline_health` AS
WITH latest_run AS (
  SELECT * FROM `jarvis-bhaga-prod.bhaga.pipeline_runs`
  ORDER BY recorded_at_utc DESC LIMIT 1
),
freshness AS (
  SELECT
    (SELECT MAX(scraped_at_utc)  FROM `jarvis-bhaga-prod.bhaga.square_transactions`) AS square_last_pull_utc,
    (SELECT MAX(date_local)      FROM `jarvis-bhaga-prod.bhaga.square_transactions`) AS square_last_data_date,
    (SELECT MAX(scraped_at_utc)  FROM `jarvis-bhaga-prod.bhaga.adp_shifts`)          AS adp_last_pull_utc,
    (SELECT MAX(date)            FROM `jarvis-bhaga-prod.bhaga.adp_shifts`)           AS adp_last_data_date,
    (SELECT MAX(ingested_at_utc) FROM `jarvis-bhaga-prod.bhaga.google_reviews`)      AS reviews_last_pull_utc,
    (SELECT MAX(post_date_ct)    FROM `jarvis-bhaga-prod.bhaga.google_reviews`)      AS reviews_last_data_date
)
SELECT
  r.run_date, r.started_at_utc, r.finished_at_utc, r.runtime_s,
  r.status, r.failed_step, r.error,
  f.*
FROM freshness f
LEFT JOIN latest_run r ON TRUE;
