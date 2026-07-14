-- 038_tip_exemption_windows.sql
-- Issue #167: tip exemptions may be whole-shift OR a time window inside a shift.
-- NULL/NULL exempt_start/exempt_end = whole-day (legacy training_shifts behavior).
-- Both set (HH:MM America/Chicago) = partial tip-hour exclusion via overlap.

ALTER TABLE `jarvis-bhaga-prod.bhaga.training_shifts`
  ADD COLUMN IF NOT EXISTS exempt_start STRING;

ALTER TABLE `jarvis-bhaga-prod.bhaga.training_shifts`
  ADD COLUMN IF NOT EXISTS exempt_end STRING;

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_training_shifts` AS
SELECT employee_name, date, exempt_start, exempt_end, note, updated_at, updated_by
FROM `jarvis-bhaga-prod.bhaga.training_shifts`
WHERE store = 'palmetto'
ORDER BY date DESC, employee_name
