-- 037_plaid_transactions.sql
-- Issue #158: Plaid Link + /transactions/sync ledger for Operator Console
-- Accounting (Square revenue in / Plaid cash out). Custom management
-- categorization is a follow-up — this stores Plaid PFC v2 as interim.
--
-- plaid_items — one linked Item per store (v1: single business checking).
--   cursor is the /transactions/sync pagination cursor (empty = full hist).
--   access_token lives in Secret Manager (plaid_access_token_<item_id>), never BQ.
-- plaid_transactions — idempotent MERGE on transaction_id; amount is Plaid's
--   signed dollars (positive = money out / outflow for depository accounts).
-- vw_plaid_spend_by_category_daily — outflows only, by pfc_primary.

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.plaid_items` (
  store              STRING    NOT NULL,
  item_id            STRING    NOT NULL,
  institution_name   STRING,
  cursor             STRING,
  linked_at          TIMESTAMP,
  linked_by          STRING,
  last_synced_at     TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.plaid_transactions` (
  transaction_id     STRING    NOT NULL,
  item_id            STRING    NOT NULL,
  account_id         STRING,
  date               DATE,
  name               STRING,
  merchant_name      STRING,
  amount             FLOAT64,
  iso_currency       STRING,
  pending            BOOL,
  pfc_primary        STRING,
  pfc_detailed       STRING,
  raw_json           STRING,
  updated_at         TIMESTAMP
);

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_plaid_spend_by_category_daily` AS
SELECT
  date,
  COALESCE(pfc_primary, 'UNCATEGORIZED') AS pfc_primary,
  SUM(IF(amount > 0, amount, 0)) AS spend,
  COUNT(*) AS txn_count
FROM `jarvis-bhaga-prod.bhaga.plaid_transactions`
WHERE pending IS NOT TRUE
GROUP BY date, pfc_primary;
