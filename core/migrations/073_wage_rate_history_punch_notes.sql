-- 073_wage_rate_history_punch_notes.sql
-- Issue #343: effective-dated hourly rates + ADP punch notes.
--
-- adp_wage_rates keeps ONE current rate per employee (and the Mon/Tue earnings
-- load rewrites it with the last paycheck's rate), so it cannot price a past
-- shift. adp_wage_rate_history records each change with the date it took
-- effect; vw_wage_rate_effective turns that into [effective_from, effective_to]
-- ranges for every wage consumer (074).
--
-- Seeded once from today's adp_wage_rates at 2000-01-01 so every employee
-- without a recorded change prices exactly as before.
--
-- adp_punches.note carries the ADP Timecard "Notes" cell; punches whose note
-- matches store_config.tip_exempt_punch_note_keywords (default 'admin') earn
-- no tips (materialize_model_bq).
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c
--   "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.adp_wage_rate_history` (
  employee_id       STRING NOT NULL,
  effective_date    DATE   NOT NULL,
  wage_rate_dollars FLOAT64,
  ot_rate_dollars   FLOAT64,
  source            STRING,
  observed_at_utc   TIMESTAMP,
  note              STRING
);

INSERT INTO `jarvis-bhaga-prod.bhaga.adp_wage_rate_history`
  (employee_id, effective_date, wage_rate_dollars, ot_rate_dollars, source, observed_at_utc, note)
SELECT
  w.employee_id,
  DATE '2000-01-01',
  w.wage_rate_dollars,
  w.ot_rate_dollars,
  'seed',
  CURRENT_TIMESTAMP(),
  'migration 073 seed from adp_wage_rates'
FROM `jarvis-bhaga-prod.bhaga.adp_wage_rates` w
WHERE w.wage_rate_dollars IS NOT NULL
  AND IFNULL(w.employee_id, '') != ''
  AND NOT EXISTS (
    SELECT 1
    FROM `jarvis-bhaga-prod.bhaga.adp_wage_rate_history` h
    WHERE h.employee_id = w.employee_id
  );

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_wage_rate_effective` AS
SELECT
  employee_id,
  effective_date AS effective_from,
  COALESCE(
    DATE_SUB(
      LEAD(effective_date) OVER (PARTITION BY employee_id ORDER BY effective_date),
      INTERVAL 1 DAY
    ),
    DATE '9999-12-31'
  ) AS effective_to,
  wage_rate_dollars,
  ot_rate_dollars,
  source
FROM `jarvis-bhaga-prod.bhaga.adp_wage_rate_history`;

ALTER TABLE `jarvis-bhaga-prod.bhaga.adp_punches`
  ADD COLUMN IF NOT EXISTS note STRING;
