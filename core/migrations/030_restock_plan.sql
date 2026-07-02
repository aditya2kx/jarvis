-- 030_restock_plan.sql
-- Issue #137: operator-provided restock schedule (delivery dates) + actual
-- placed-order quantities. Written by the /bhaga-cloud restock Slack modal
-- (cloud/webhook/handler.py). Read by migration 031's dual-date reco views.
--
-- inventory_restock_schedule -- the calendar delivery dates being tracked.
--   Merge key: (store, delivery_date). A date is "registered" here whether
--   or not actuals have been uploaded for it yet.
--
-- inventory_restock_orders -- actual placed-order quantities per item, once
--   the operator has an order confirmed for a delivery date. Replace-per-date
--   semantics: the handler DELETEs all rows for (store, delivery_date) then
--   INSERTs the freshly-parsed CSV rows, so "Add order" for a date is always
--   idempotent (re-uploading a corrected CSV converges, never dupes).
--   Absence of rows for a date means "no actuals yet" -- migration 031's
--   TVFs fall back to the estimated water-fill for that date.

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.inventory_restock_schedule` (
  store          STRING  NOT NULL,
  delivery_date  DATE    NOT NULL,
  updated_by     STRING,
  updated_at     TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.inventory_restock_orders` (
  store          STRING   NOT NULL,
  delivery_date  DATE     NOT NULL,
  item           STRING   NOT NULL,
  quantity_tubs  FLOAT64,
  updated_by     STRING,
  updated_at     TIMESTAMP
);
