-- 091: probe table for the Operator Console query cache (ARCHITECTURE.md §15)
-- Nothing reads it but GET /api/cache-probe, which reads it *through* the cache. Writing a
-- row from outside the console (Cloud Shell `bq query`, any writer) must reach a page within
-- the audit-log delay: BigQuery audit log → Pub/Sub → bhaga-webhook /cache/bq-write bumps
-- console_cache_versions/console_cache_probe → the next probe read misses and sees the row.
-- RUNBOOK.md §17 "Query cache" has the probe recipe.
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c \
--   "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.console_cache_probe` (
  n           INT64 NOT NULL,
  written_by  STRING NOT NULL,
  written_at  TIMESTAMP NOT NULL
);
