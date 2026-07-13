-- 036_inventory_base_runway_dual.sql
-- Issue #164: Base runway considers up to 2 future **Actuals** restock dates
-- (inventory_restock_orders), with dual stockout dates and dual Risky/Fine
-- status. Estimated schedule dates do NOT appear (operator jam clarification
-- on PR #165). Console-only — no Grafana panel.
--
-- Semantics (operator-locked):
--   Restock 1/2  = 1st / 2nd future Actuals delivery_date per base from
--                  inventory_restock_orders (never estimated-only schedule)
--   Qty 1/2      = SUM(quantity_tubs) for that base on that Actuals date
--   Days left    = burn-down from today, ignores future restocks
--                  (current_qty / avg_daily_usage), 1 decimal — unchanged
--   Stockout 1   = today_CT + FLOOR(days_left); today when days_left <= 0
--   Stockout 2   = chain after slot 1: on_hand_at_d1 + COALESCE(qty1,0)
--                  burned from Restock 1 at vel → Restock 1 + FLOOR(days_after);
--                  NULL when no Restock 1 (cannot chain)
--   Status N     = Risky when restock N is empty OR stockout N is empty OR
--                  stockout N < restock N (i.e. restock arrives after stockout);
--                  Fine when restock N <= stockout N (same-day = Fine);
--                  Status 2 NULL when no Restock 2 column value expected
--                  (no second Actuals date) — UI leaves badge empty
--
-- Excludes Blade (inactive). Includes bases with null/zero avg usage
-- (Days left null → Status 1 Risky).

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
with_stockout1 AS (
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
    END AS stockout_1
  FROM oa
),
-- Per-item future Actuals dates, oldest first (cap 2).
actual_slots AS (
  SELECT
    item,
    delivery_date,
    ROW_NUMBER() OVER (PARTITION BY item ORDER BY delivery_date) AS slot
  FROM (
    SELECT DISTINCT item, delivery_date
    FROM `jarvis-bhaga-prod.bhaga.inventory_restock_orders`
    WHERE store = 'palmetto'
      AND delivery_date >= CURRENT_DATE('America/Chicago')
  )
),
qty_by_date AS (
  SELECT
    item,
    delivery_date,
    SUM(quantity_tubs) AS restock_qty
  FROM `jarvis-bhaga-prod.bhaga.inventory_restock_orders`
  WHERE store = 'palmetto'
    AND delivery_date >= CURRENT_DATE('America/Chicago')
  GROUP BY item, delivery_date
),
joined AS (
  SELECT
    w.item,
    w.current_qty,
    w.avg_daily_usage,
    w.days_left,
    w.stockout_1,
    a1.delivery_date AS restock_1,
    q1.restock_qty AS qty_1,
    a2.delivery_date AS restock_2,
    q2.restock_qty AS qty_2,
    GREATEST(
      w.current_qty
        - DATE_DIFF(a1.delivery_date, CURRENT_DATE('America/Chicago'), DAY)
          * w.avg_daily_usage,
      0
    ) AS on_hand_at_d1
  FROM with_stockout1 w
  LEFT JOIN actual_slots a1
    ON a1.item = w.item AND a1.slot = 1
  LEFT JOIN actual_slots a2
    ON a2.item = w.item AND a2.slot = 2
  LEFT JOIN qty_by_date q1
    ON q1.item = w.item AND q1.delivery_date = a1.delivery_date
  LEFT JOIN qty_by_date q2
    ON q2.item = w.item AND q2.delivery_date = a2.delivery_date
),
with_stockout2 AS (
  SELECT
    *,
    CASE
      WHEN restock_1 IS NULL THEN NULL
      WHEN avg_daily_usage = 0 THEN NULL
      WHEN SAFE_DIVIDE(
             on_hand_at_d1 + COALESCE(qty_1, 0),
             NULLIF(avg_daily_usage, 0)
           ) <= 0
        THEN restock_1
      ELSE DATE_ADD(
        restock_1,
        INTERVAL CAST(
          FLOOR(
            SAFE_DIVIDE(
              on_hand_at_d1 + COALESCE(qty_1, 0),
              avg_daily_usage
            )
          ) AS INT64
        ) DAY
      )
    END AS stockout_2
  FROM joined
)
SELECT
  j.item AS Base,
  ROUND(j.current_qty, 1) AS Stock,
  ROUND(j.avg_daily_usage, 2) AS `Vel per day`,
  j.days_left AS `Days left`,
  j.stockout_1 AS `Stockout 1`,
  j.restock_1 AS `Restock 1`,
  j.qty_1 AS `Qty 1`,
  CASE
    WHEN j.restock_1 IS NULL THEN 'Risky'
    WHEN j.stockout_1 IS NULL THEN 'Risky'
    WHEN j.stockout_1 < j.restock_1 THEN 'Risky'
    ELSE 'Fine'
  END AS `Status 1`,
  j.stockout_2 AS `Stockout 2`,
  j.restock_2 AS `Restock 2`,
  j.qty_2 AS `Qty 2`,
  CASE
    -- Operator (PR #165): empty restock → Risky for that slot.
    WHEN j.restock_2 IS NULL THEN 'Risky'
    WHEN j.stockout_2 IS NULL THEN 'Risky'
    WHEN j.stockout_2 < j.restock_2 THEN 'Risky'
    ELSE 'Fine'
  END AS `Status 2`
FROM with_stockout2 j
ORDER BY j.days_left ASC NULLS LAST, j.current_qty DESC;
