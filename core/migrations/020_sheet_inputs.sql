-- 020_sheet_inputs.sql
-- BQ homes for the last human inputs leaving Google Sheets:
--   training_shifts  (per-shift training marks; was the Sheet `training_shifts` tab)
--   employee_aliases (raw->canonical name map; was the Sheet `employees` tab)
-- training_excluded:<name> and excluded_from_tip_pool stay in store_config (007).
-- Apply: BHAGA_DATASTORE=bigquery python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.training_shifts` (
  store         STRING    NOT NULL,
  employee_name STRING    NOT NULL,
  date          DATE      NOT NULL,
  note          STRING,
  updated_at    TIMESTAMP,
  updated_by    STRING
);

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.employee_aliases` (
  store          STRING    NOT NULL,
  raw_name       STRING    NOT NULL,
  canonical_name STRING    NOT NULL,
  notes          STRING,
  updated_at     TIMESTAMP,
  updated_by     STRING
);

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_training_shifts` AS
SELECT employee_name, date, note, updated_at, updated_by
FROM `jarvis-bhaga-prod.bhaga.training_shifts`
WHERE store = 'palmetto'
ORDER BY date DESC, employee_name
