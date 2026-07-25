-- 045_plaid_taxonomy_rules.sql
-- Palmetto management taxonomy + Copilot-style category rules (Issue #160).
-- Keeps pfc_* on plaid_transactions for debug; operator-facing category is
-- category_id / subcategory_id (override_* beats rule).

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.plaid_taxonomy_nodes` (
  id STRING NOT NULL,
  parent_id STRING,
  slug STRING NOT NULL,
  label STRING NOT NULL,
  definition STRING,
  default_pnl_treatment STRING,
  sort_order INT64,
  enabled BOOL,
  updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.plaid_category_rules` (
  id STRING NOT NULL,
  priority INT64 NOT NULL,
  match_field STRING NOT NULL,
  match_operator STRING NOT NULL,
  match_pattern STRING NOT NULL,
  amount_sign STRING,
  category_id STRING NOT NULL,
  subcategory_id STRING,
  confidence STRING,
  enabled BOOL,
  notes STRING,
  updated_at TIMESTAMP
);

ALTER TABLE `jarvis-bhaga-prod.bhaga.plaid_transactions`
ADD COLUMN IF NOT EXISTS category_id STRING;

ALTER TABLE `jarvis-bhaga-prod.bhaga.plaid_transactions`
ADD COLUMN IF NOT EXISTS subcategory_id STRING;

ALTER TABLE `jarvis-bhaga-prod.bhaga.plaid_transactions`
ADD COLUMN IF NOT EXISTS rule_id STRING;

ALTER TABLE `jarvis-bhaga-prod.bhaga.plaid_transactions`
ADD COLUMN IF NOT EXISTS override_category_id STRING;

ALTER TABLE `jarvis-bhaga-prod.bhaga.plaid_transactions`
ADD COLUMN IF NOT EXISTS override_subcategory_id STRING;

ALTER TABLE `jarvis-bhaga-prod.bhaga.plaid_transactions`
ADD COLUMN IF NOT EXISTS categorized_at TIMESTAMP;
