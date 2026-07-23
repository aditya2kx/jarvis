-- 044_plaid_internal_flag.sql
-- Phase A Accounting: mark intra-Item transfers (e.g. checking → own Chase card)
-- so Money out / category rollups can skip them. Operator can toggle in the console.

ALTER TABLE `jarvis-bhaga-prod.bhaga.plaid_transactions`
ADD COLUMN IF NOT EXISTS is_internal BOOL;

-- Backfill defaults (NULL → false) for older rows.
UPDATE `jarvis-bhaga-prod.bhaga.plaid_transactions`
SET is_internal = FALSE
WHERE is_internal IS NULL;

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_plaid_spend_by_category_daily` AS
SELECT
  date,
  COALESCE(pfc_primary, 'UNCATEGORIZED') AS pfc_primary,
  SUM(IF(amount > 0, amount, 0)) AS spend,
  COUNT(*) AS txn_count
FROM `jarvis-bhaga-prod.bhaga.plaid_transactions`
WHERE pending IS NOT TRUE
  AND IFNULL(is_internal, FALSE) IS NOT TRUE
GROUP BY date, pfc_primary;
