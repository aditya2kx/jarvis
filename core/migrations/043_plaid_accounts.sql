-- 043_plaid_accounts.sql
-- Issue #168 follow-up: persist Plaid /accounts/get mask (last-4) + name so
-- Operator Console Accounting can show which checking/card a txn hit.
-- Populated on Link exchange + each Sync (skills/plaid_api + console).

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.plaid_accounts` (
  account_id         STRING    NOT NULL,
  item_id            STRING    NOT NULL,
  name               STRING,
  mask               STRING,
  type               STRING,
  subtype            STRING,
  updated_at         TIMESTAMP
);
