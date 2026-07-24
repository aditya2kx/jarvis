-- 046_plaid_spend_view_effective.sql
-- Home / ops spend rollup by Palmetto effective category (Issue #160).
-- Effective = COALESCE(override_category_id, category_id); PFC no longer grouped.

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_plaid_spend_by_category_daily` AS
SELECT
  t.date,
  COALESCE(c.label, 'Uncategorized') AS category_label,
  COALESCE(c.slug, 'uncategorized') AS category_slug,
  -- Backward-compat alias so older readers that select pfc_primary still work
  -- until cut over (maps to effective category label, not Plaid PFC).
  COALESCE(c.label, 'Uncategorized') AS pfc_primary,
  SUM(IF(t.amount > 0, t.amount, 0)) AS spend,
  COUNT(*) AS txn_count
FROM `jarvis-bhaga-prod.bhaga.plaid_transactions` t
LEFT JOIN `jarvis-bhaga-prod.bhaga.plaid_taxonomy_nodes` c
  ON c.id = COALESCE(t.override_category_id, t.category_id)
WHERE t.pending IS NOT TRUE
  AND IFNULL(t.is_internal, FALSE) IS NOT TRUE
GROUP BY t.date, category_label, category_slug, pfc_primary;
