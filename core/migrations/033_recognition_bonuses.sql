-- 033_recognition_bonuses.sql
-- Issue #132 (operator console M4): manual per-employee recognition bonus,
-- separate from the automated review-bonus pipeline (migration 026). Written
-- by the operator console's Recognition drawer; read into the Payroll
-- screen and reconciled against the ADP bonus earnings line.
--
-- recognition_bonuses -- one row per (store, pay_period, employee). Merge
--   key matches training_shifts' contract (idempotent re-submit, no dupes).
--   amount_cents is integer cents (invariant) -- console formatting must use
--   formatCents, never formatDollars, for this column.

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.recognition_bonuses` (
  store          STRING  NOT NULL,
  pay_period     STRING  NOT NULL,   -- e.g. '2026-07-01..2026-07-15'
  employee       STRING  NOT NULL,
  amount_cents   INT64   NOT NULL,
  reason         STRING,
  updated_by     STRING,
  updated_at     TIMESTAMP
);

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_recognition_bonuses` AS
SELECT * FROM `jarvis-bhaga-prod.bhaga.recognition_bonuses`;
