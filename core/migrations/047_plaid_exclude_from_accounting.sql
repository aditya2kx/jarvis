-- 047_plaid_exclude_from_accounting.sql
-- Exclude-from-accounting on taxonomy; Internal transfers seed; account_mask on
-- rules; spend + money-in views honor effective exclude (Issue #189).
-- NULL exclude_from_accounting = inherit parent; TRUE/FALSE = explicit.

ALTER TABLE `jarvis-bhaga-prod.bhaga.plaid_taxonomy_nodes`
ADD COLUMN IF NOT EXISTS exclude_from_accounting BOOL;

ALTER TABLE `jarvis-bhaga-prod.bhaga.plaid_category_rules`
ADD COLUMN IF NOT EXISTS account_mask STRING;

-- Seed Internal transfers parent (exclude from business rollups).
MERGE `jarvis-bhaga-prod.bhaga.plaid_taxonomy_nodes` T
USING (
  SELECT
    'internal_transfers' AS id,
    CAST(NULL AS STRING) AS parent_id,
    'internal_transfers' AS slug,
    'Internal transfers' AS label,
    'Bank↔bank or checking↔card payments between linked accounts. Excluded from accounting totals.' AS definition,
    CAST(NULL AS STRING) AS default_pnl_treatment,
    TRUE AS exclude_from_accounting,
    10 AS sort_order,
    TRUE AS enabled
) S
ON T.id = S.id
WHEN MATCHED THEN UPDATE SET
  label = S.label,
  definition = S.definition,
  exclude_from_accounting = S.exclude_from_accounting,
  enabled = S.enabled,
  updated_at = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (
  id, parent_id, slug, label, definition, default_pnl_treatment,
  exclude_from_accounting, sort_order, enabled, updated_at
) VALUES (
  S.id, S.parent_id, S.slug, S.label, S.definition, S.default_pnl_treatment,
  S.exclude_from_accounting, S.sort_order, S.enabled, CURRENT_TIMESTAMP()
);

-- Migrate legacy is_internal bool → Internal transfers category (mirror bool kept).
UPDATE `jarvis-bhaga-prod.bhaga.plaid_transactions`
SET
  category_id = 'internal_transfers',
  subcategory_id = NULL,
  rule_id = NULL,
  categorized_at = CURRENT_TIMESTAMP(),
  updated_at = CURRENT_TIMESTAMP()
WHERE IFNULL(is_internal, FALSE) IS TRUE
  AND override_category_id IS NULL
  AND IFNULL(category_id, '') != 'internal_transfers';

-- Business spend by parent taxonomy category (excludes pending + effective_exclude).
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_plaid_spend_by_category_daily` AS
WITH base AS (
  SELECT
    t.date,
    t.amount,
    COALESCE(
      t.override_subcategory_id,
      t.subcategory_id,
      t.override_category_id,
      t.category_id
    ) AS leaf_id
  FROM `jarvis-bhaga-prod.bhaga.plaid_transactions` t
  WHERE t.pending IS NOT TRUE
),
resolved AS (
  SELECT
    b.date,
    b.amount,
    leaf.id AS leaf_id,
    COALESCE(leaf.parent_id, leaf.id) AS parent_id,
    COALESCE(
      leaf.exclude_from_accounting,
      CASE WHEN leaf.parent_id IS NOT NULL THEN parent.exclude_from_accounting END,
      FALSE
    ) AS effective_exclude,
    COALESCE(parent.label, leaf.label, 'Uncategorized') AS category_label,
    COALESCE(parent.slug, leaf.slug, 'uncategorized') AS category_slug,
    COALESCE(parent.id, leaf.id) AS category_id
  FROM base b
  LEFT JOIN `jarvis-bhaga-prod.bhaga.plaid_taxonomy_nodes` leaf
    ON leaf.id = b.leaf_id
  LEFT JOIN `jarvis-bhaga-prod.bhaga.plaid_taxonomy_nodes` parent
    ON parent.id = leaf.parent_id
)
SELECT
  date,
  category_label,
  category_slug,
  category_label AS pfc_primary,
  category_id,
  SUM(IF(amount > 0, amount, 0)) AS spend,
  COUNT(*) AS txn_count
FROM resolved
WHERE IFNULL(effective_exclude, FALSE) IS NOT TRUE
GROUP BY date, category_label, category_slug, category_id, pfc_primary;

-- Bank money-in (Plaid amount < 0) excluding pending + effective_exclude.
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_plaid_money_in_daily` AS
WITH base AS (
  SELECT
    t.date,
    t.amount,
    COALESCE(
      t.override_subcategory_id,
      t.subcategory_id,
      t.override_category_id,
      t.category_id
    ) AS leaf_id
  FROM `jarvis-bhaga-prod.bhaga.plaid_transactions` t
  WHERE t.pending IS NOT TRUE
    AND t.amount < 0
),
resolved AS (
  SELECT
    b.date,
    ABS(b.amount) AS money_in,
    COALESCE(
      leaf.exclude_from_accounting,
      CASE WHEN leaf.parent_id IS NOT NULL THEN parent.exclude_from_accounting END,
      FALSE
    ) AS effective_exclude
  FROM base b
  LEFT JOIN `jarvis-bhaga-prod.bhaga.plaid_taxonomy_nodes` leaf
    ON leaf.id = b.leaf_id
  LEFT JOIN `jarvis-bhaga-prod.bhaga.plaid_taxonomy_nodes` parent
    ON parent.id = leaf.parent_id
)
SELECT
  date,
  SUM(money_in) AS money_in,
  COUNT(*) AS txn_count
FROM resolved
WHERE IFNULL(effective_exclude, FALSE) IS NOT TRUE
GROUP BY date;
