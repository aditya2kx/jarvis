-- 036_inventory_base_runway_dual.sql
-- Issue #164: Base runway considers the same 2 future restock dates as
-- Next delivery (vw_order_reco_next_dates), with dual stockout dates and
-- dual Risky/Fine status. Console-only — no Grafana panel.
--
-- Semantics (jam-locked):
--   Restock 1/2  = global schedule slots from vw_order_reco_next_dates
--                  (Estimated or Actuals — same dates as Next delivery)
--   Qty 1/2      = SUM(quantity_tubs) from inventory_restock_orders on that
--                  date for the base; NULL when no Actuals
--   Days left    = burn-down from today, ignores future restocks
--                  (current_qty / avg_daily_usage), 1 decimal — unchanged
--   Stockout 1   = today_CT + FLOOR(days_left); today when days_left <= 0
--   Stockout 2   = chain after slot 1: on_hand_at_d1 + COALESCE(qty1,0)
--                  burned from d1 at vel → d1 + FLOOR(days_after);
--                  NULL when no slot 2
--   Status 1     = Fine iff Actuals exist for slot 1 AND restock_1 <= stockout_1;
--                  else Risky (Actuals-only Fine — preserve #156)
--   Status 2     = Fine iff slot 2 + Actuals on slot 2 AND restock_2 <= stockout_2;
--                  NULL when no slot 2; else Risky
--
-- Excludes Blade (inactive). Includes bases with null/zero avg usage
-- (Days left null → Status 1 Risky).

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_inventory_base_runway` AS
WITH
slots AS (
  SELECT
    MAX(IF(slot = 1, delivery_date, NULL)) AS d1,
    MAX(IF(slot = 2, delivery_date, NULL)) AS d2
  FROM `jarvis-bhaga-prod.bhaga.vw_order_reco_next_dates`
),
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
    s.d1 AS restock_1,
    q1.restock_qty AS qty_1,
    s.d2 AS restock_2,
    q2.restock_qty AS qty_2,
    -- on_hand at D1 arrival (same math as tvf_order_reco_slot1)
    GREATEST(
      w.current_qty
        - DATE_DIFF(s.d1, CURRENT_DATE('America/Chicago'), DAY)
          * w.avg_daily_usage,
      0
    ) AS on_hand_at_d1
  FROM with_stockout1 w
  CROSS JOIN slots s
  LEFT JOIN qty_by_date q1
    ON q1.item = w.item AND q1.delivery_date = s.d1
  LEFT JOIN qty_by_date q2
    ON q2.item = w.item AND q2.delivery_date = s.d2
),
with_stockout2 AS (
  SELECT
    *,
    CASE
      WHEN restock_2 IS NULL THEN NULL
      WHEN restock_1 IS NULL THEN NULL
      WHEN avg_daily_usage = 0 THEN NULL
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
    END AS stockout_2_raw
  FROM joined
),
with_stockout2_clamped AS (
  SELECT
    * EXCEPT (stockout_2_raw),
    CASE
      WHEN stockout_2_raw IS NULL THEN NULL
      -- days-after <= 0 → stockout on d1
      WHEN SAFE_DIVIDE(
             on_hand_at_d1 + COALESCE(qty_1, 0),
             NULLIF(avg_daily_usage, 0)
           ) <= 0
        THEN restock_1
      ELSE stockout_2_raw
    END AS stockout_2
  FROM with_stockout2
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
    WHEN j.qty_1 IS NULL THEN 'Risky'
    WHEN j.stockout_1 IS NULL THEN 'Risky'
    WHEN j.restock_1 > j.stockout_1 THEN 'Risky'
    ELSE 'Fine'
  END AS `Status 1`,
  j.stockout_2 AS `Stockout 2`,
  j.restock_2 AS `Restock 2`,
  j.qty_2 AS `Qty 2`,
  CASE
    WHEN j.restock_2 IS NULL THEN NULL
    WHEN j.qty_2 IS NULL THEN 'Risky'
    WHEN j.stockout_2 IS NULL THEN 'Risky'
    WHEN j.restock_2 > j.stockout_2 THEN 'Risky'
    ELSE 'Fine'
  END AS `Status 2`
FROM with_stockout2_clamped j
ORDER BY j.days_left ASC NULLS LAST, j.current_qty DESC;
