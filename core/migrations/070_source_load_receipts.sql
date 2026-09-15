-- 070_source_load_receipts.sql
-- Durable proof that a scraped export was PARSED AND UPSERTED into BQ for a
-- refresh_date.
--
-- Distinct from source_pulls (017), which records a scrape ATTEMPT. The two are
-- not interchangeable: a scrape can succeed and set its Firestore step marker
-- while the export file it produced dies with the Cloud Run container, because
-- extracted/downloads/ is container-local and is never uploaded to GCS. On
-- 2026-09-14 that combination let a rerun skip ADP entirely, load nothing, and
-- still report success. A scrape gate must therefore consult the durable sink,
-- never the ephemeral action.
--
-- rows_upserted = 0 is a VALID receipt: a store-closed day legitimately parses a
-- timecard containing no shifts. Writing the receipt anyway is what stops the
-- gate from re-scraping (and re-prompting for OTP) every quiet day.
--
-- Applied via: BHAGA_DATASTORE=bigquery python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.source_load_receipts` (
  store         STRING,
  refresh_date  DATE,
  source        STRING,    -- adp_timecard | adp_schedule | adp_liability | adp_rates
  rows_upserted INT64,
  loaded_at_utc TIMESTAMP,
  run_id        STRING
)
PARTITION BY refresh_date;

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_source_load_receipts` AS
SELECT refresh_date, store, source, rows_upserted, loaded_at_utc, run_id
FROM `jarvis-bhaga-prod.bhaga.source_load_receipts`
ORDER BY loaded_at_utc DESC
LIMIT 100;
