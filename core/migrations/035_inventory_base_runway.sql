-- 035_inventory_base_runway.sql
-- Issue #156: Operator Console Base runway table (urgency / days-left from
-- today, Actuals-only next restock, Risky vs Fine status). Console-only —
-- no Grafana panel. Reads vw_inventory_order_assistant (028) +
-- inventory_restock_orders (030).
--
-- Semantics (jam-locked):
--   Days left     = burn-down from today, ignores future restocks
--                   (current_qty / avg_daily_usage), 1 decimal
--   Stockout date = today_CT + FLOOR(days_left); today when days_left <= 0
--   Next restock  = earliest future delivery_date with Actuals rows in
--                   inventory_restock_orders (never estimated-only schedule)
--   Status        = Risky when no Actuals OR next_restock > stockout;
--                   Fine when next_restock <= stockout (same-day = Fine)
--
-- Excludes Blade (inactive). Includes bases with null/zero avg usage
-- (Days left null → Status Risky).

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_inventory_base_runway` AS
WITH
oa AS (
  SELECT
    item,
    current_qty,
    COALESCE(avg_daily_usage, 0) AS avg_daily_usage,
    ROUND(
      SAFE_DIVIDE(current_qty, NULLIF(avg_daily_usage, 0)),
      1
    ) AS days_left
  FROM `jarvis-bhaga-prod.bhaga.vw_inventory_order_assistant`
  WHERE store = 'palmetto' AND item != 'Blade'
),
with_stockout AS (
  SELECT
    item,
    current_qty,
    avg_daily_usage,
    days_left,
    CASE
      WHEN days_left IS NULL THEN NULL
      WHEN days_left <= 0 THEN CURRENT_DATE('America/Chicago')
      ELSE DATE_ADD(
        CURRENT_DATE('America/Chicago'),
        INTERVAL CAST(FLOOR(days_left) AS INT64) DAY
      )
    END AS stockout_date
  FROM oa
),
next_actual AS (
  SELECT
    item,
    MIN(delivery_date) AS next_restock_date
  FROM `jarvis-bhaga-prod.bhaga.inventory_restock_orders`
  WHERE store = 'palmetto'
    AND delivery_date >= CURRENT_DATE('America/Chicago')
  GROUP BY item
),
next_qty AS (
  SELECT
    o.item,
    o.delivery_date,
    SUM(o.quantity_tubs) AS restock_qty
  FROM `jarvis-bhaga-prod.bhaga.inventory_restock_orders` o
  INNER JOIN next_actual n
    ON o.item = n.item AND o.delivery_date = n.next_restock_date
  WHERE o.store = 'palmetto'
  GROUP BY o.item, o.delivery_date
)
SELECT
  w.item AS Base,
  ROUND(w.current_qty, 1) AS Stock,
  ROUND(w.avg_daily_usage, 2) AS `Vel per day`,
  w.days_left AS `Days left`,
  w.stockout_date AS `Stockout date`,
  n.next_restock_date AS `Next restock`,
  q.restock_qty AS `Restock qty`,
  CASE
    WHEN n.next_restock_date IS NULL THEN 'Risky'
    WHEN w.stockout_date IS NULL THEN 'Risky'
    WHEN n.next_restock_date > w.stockout_date THEN 'Risky'
    ELSE 'Fine'
  END AS Status
FROM with_stockout w
LEFT JOIN next_actual n USING (item)
LEFT JOIN next_qty q ON q.item = w.item
ORDER BY w.days_left ASC NULLS LAST, w.current_qty DESC;
