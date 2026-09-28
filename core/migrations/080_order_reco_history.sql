-- 080_order_reco_history.sql
-- Issue #350: append-only history for the order recommendation and every
-- operator inventory edit. Rows in these tables are never UPDATEd or DELETEd
-- (enforced by scripts/check_append_only_history.py); the live table
-- inventory_order_reco keeps only the latest generation for fast reads.

-- Every generation ever committed, with the Source each row was computed with.
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.inventory_order_reco_history` (
  store STRING NOT NULL,
  run_id STRING NOT NULL,
  refreshed_at TIMESTAMP NOT NULL,
  delivery_date DATE,
  Slot INT64,
  Item STRING,
  `Current Qty` FLOAT64,
  `Avg per day` FLOAT64,
  `On Hand at Restock` FLOAT64,
  `Order Tubs` INT64,
  `Order Weight lbs` FLOAT64,
  `After Restock` FLOAT64,
  `Days Left After Restock` FLOAT64,
  _ord INT64,
  Source STRING
)
PARTITION BY DATE(refreshed_at)
CLUSTER BY store, delivery_date;

-- One row per run state change (running -> committed | superseded | failed).
-- Latest row per run_id is the run's current state.
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.inventory_order_reco_runs` (
  run_id STRING NOT NULL,
  store STRING NOT NULL,
  status STRING NOT NULL,
  event_at TIMESTAMP NOT NULL,
  trigger STRING,
  requested_by STRING,
  refreshed_at TIMESTAMP,
  inputs_fingerprint INT64,
  capacity INT64,
  as_of_date DATE,
  passes INT64,
  error STRING,
  inputs JSON  -- capacity, dates, pins, actuals, per-item model inputs
)
PARTITION BY DATE(event_at)
CLUSTER BY store, run_id;

-- Every operator edit to an order-reco input, from any writer (Operator
-- Console, Slack webhook). Each row is the entity's full state after the edit;
-- vw_inventory_edit_log derives the state before it.
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.inventory_edit_log` (
  event_id STRING NOT NULL,
  store STRING NOT NULL,
  entity STRING NOT NULL,
  action STRING NOT NULL,
  delivery_date DATE,
  item STRING,
  key STRING,
  new_value JSON,
  edited_by STRING,
  edited_at TIMESTAMP NOT NULL,
  source STRING
)
PARTITION BY DATE(edited_at)
CLUSTER BY store, entity;

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_inventory_edit_log` AS
SELECT
  *,
  LAG(new_value) OVER (
    PARTITION BY store, entity, delivery_date, item, key ORDER BY edited_at, event_id
  ) AS old_value
FROM `jarvis-bhaga-prod.bhaga.inventory_edit_log`;

-- Source is materialized with the numbers so a reader never pairs one
-- generation's numbers with another moment's pins/actuals.
ALTER TABLE `jarvis-bhaga-prod.bhaga.inventory_order_reco`
  ADD COLUMN IF NOT EXISTS Source STRING,
  ADD COLUMN IF NOT EXISTS run_id STRING;

-- Hash of every input the recommendation depends on. The refresh procedure
-- compares it before and after a pass to coalesce edits that landed mid-run;
-- the console compares it with the painted run's to show "stale".
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_order_reco_inputs_fingerprint` AS
WITH
  pins AS (
    SELECT store, STRING_AGG(FORMAT('%t:%s:%d', delivery_date, item, quantity_tubs), ',' ORDER BY delivery_date, item, quantity_tubs) AS v
    FROM `jarvis-bhaga-prod.bhaga.inventory_order_tub_overrides` GROUP BY store),
  actuals AS (
    SELECT store, STRING_AGG(FORMAT('%t:%s:%t', delivery_date, item, quantity_tubs), ',' ORDER BY delivery_date, item, quantity_tubs) AS v
    FROM `jarvis-bhaga-prod.bhaga.inventory_restock_orders` GROUP BY store),
  schedule AS (
    SELECT store, STRING_AGG(FORMAT('%t', delivery_date), ',' ORDER BY delivery_date) AS v
    FROM `jarvis-bhaga-prod.bhaga.inventory_restock_schedule` GROUP BY store),
  qty AS (
    SELECT store, STRING_AGG(FORMAT('%s:%t', item, quantity_units), ',' ORDER BY item, quantity_units) AS v
    FROM `jarvis-bhaga-prod.bhaga.inventory_current_qty_overrides` GROUP BY store),
  usage_days AS (
    SELECT store, STRING_AGG(FORMAT('%s:%t:%s', item, submitted_date, mode), ',' ORDER BY item, submitted_date, mode) AS v
    FROM `jarvis-bhaga-prod.bhaga.inventory_usage_day_overrides` GROUP BY store),
  closings AS (
    SELECT store, FORMAT('%d:%t:%t', COUNT(*), ROUND(SUM(quantity_units), 4), MAX(submitted_date)) AS v
    FROM `jarvis-bhaga-prod.bhaga.inventory_closing_daily` GROUP BY store),
  config AS (
    SELECT store, STRING_AGG(FORMAT('%s=%s', key, value), ',' ORDER BY key) AS v
    FROM (
      SELECT store, key, value FROM `jarvis-bhaga-prod.bhaga.store_config`
      WHERE key IN ('order_reco_max_tubs', 'order_reco_max_slots')
      QUALIFY ROW_NUMBER() OVER (PARTITION BY store, key ORDER BY updated_at DESC) = 1)
    GROUP BY store)
SELECT
  store,
  FARM_FINGERPRINT(CONCAT(
    'date=', CAST(CURRENT_DATE('America/Chicago') AS STRING),
    '|pins=', COALESCE(pins.v, ''),
    '|actuals=', COALESCE(actuals.v, ''),
    '|schedule=', COALESCE(schedule.v, ''),
    '|qty=', COALESCE(qty.v, ''),
    '|usage=', COALESCE(usage_days.v, ''),
    '|closings=', COALESCE(closings.v, ''),
    '|config=', COALESCE(config.v, '')
  )) AS inputs_fingerprint
FROM closings
LEFT JOIN pins USING (store)
LEFT JOIN actuals USING (store)
LEFT JOIN schedule USING (store)
LEFT JOIN qty USING (store)
LEFT JOIN usage_days USING (store)
LEFT JOIN config USING (store);
