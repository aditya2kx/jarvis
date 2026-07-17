-- 041_order_reco_delivery_date.sql
-- Fix Operator Console /inventory mislabeled columns (Issue #178 follow-on):
-- live vw_order_reco_next_dates headers were painted onto stale Slot 1/2 rows
-- from inventory_order_reco (materialized when Slot 1 still meant an earlier
-- calendar date). Also: next_dates used >= today so a restock day stayed in
-- Slot 1 after ClickUp closing already included received tubs → After Restock
-- double-counted.
--
-- Changes:
--   1. Persist delivery_date on inventory_order_reco (written by TVFs).
--   2. next_dates uses delivery_date > today CT (strictly future planning).
--   3. TVFs emit delivery_date; slot2 still reads slot1 from the table.
--   4. vw_order_reco_combined joins by delivery_date = live next dates
--      (not Slot alone), so headers and quantities cannot desync.
--
-- refresh_order_reco / console writes.ts / handler._refresh_order_reco must
-- INSERT with an explicit column list including delivery_date (see
-- core/order_reco.py) — SELECT store, slot, t.*, ts would mis-map columns
-- after ALTER ADD.

ALTER TABLE `jarvis-bhaga-prod.bhaga.inventory_order_reco`
ADD COLUMN IF NOT EXISTS delivery_date DATE;

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_order_reco_next_dates` AS
SELECT delivery_date, slot
FROM (
  SELECT
    delivery_date,
    ROW_NUMBER() OVER (ORDER BY delivery_date) AS slot
  FROM (
    SELECT DISTINCT delivery_date
    FROM `jarvis-bhaga-prod.bhaga.inventory_restock_schedule`
    WHERE store = 'palmetto'
      AND delivery_date > CURRENT_DATE('America/Chicago')
  )
)
WHERE slot <= 2;

CREATE OR REPLACE TABLE FUNCTION `jarvis-bhaga-prod.bhaga.tvf_order_reco_slot1`(
  max_tubs INT64
) AS (
  WITH
  dd AS (
    SELECT MAX(IF(slot = 1, delivery_date, NULL)) AS d1
    FROM `jarvis-bhaga-prod.bhaga.vw_order_reco_next_dates`
  ),
  oa AS (
    SELECT
      o.item, o.current_qty,
      COALESCE(o.avg_daily_usage, 0) AS avg_daily_usage,
      GREATEST(
        o.current_qty - DATE_DIFF(dd.d1, CURRENT_DATE('America/Chicago'), DAY) * COALESCE(o.avg_daily_usage, 0),
        0
      ) AS on_hand_arrival
    FROM `jarvis-bhaga-prod.bhaga.vw_inventory_order_assistant` o, dd
    WHERE o.store = 'palmetto' AND dd.d1 IS NOT NULL
  ),
  actuals AS (
    SELECT item, SUM(quantity_tubs) AS actual_tubs
    FROM `jarvis-bhaga-prod.bhaga.inventory_restock_orders`, dd
    WHERE store = 'palmetto' AND delivery_date = dd.d1
    GROUP BY item
  ),
  has_actuals AS (SELECT COUNT(*) > 0 AS is_actual FROM actuals),
  budget AS (
    SELECT GREATEST(CAST(FLOOR(max_tubs - SUM(on_hand_arrival)) AS INT64), 0) AS tubs_budget
    FROM oa
  ),
  candidates AS (
    SELECT o.item, (o.on_hand_arrival + k - 1) / o.avg_daily_usage AS sort_key
    FROM oa o
    CROSS JOIN UNNEST(GENERATE_ARRAY(1, 300)) AS k
    CROSS JOIN budget b
    WHERE o.item != 'Blade' AND o.avg_daily_usage > 0 AND k <= b.tubs_budget
  ),
  ranked AS (
    SELECT item, ROW_NUMBER() OVER (ORDER BY sort_key ASC) AS rn FROM candidates
  ),
  est_selected AS (
    SELECT item, COUNT(*) AS order_tubs
    FROM ranked
    CROSS JOIN budget b
    WHERE rn <= b.tubs_budget
    GROUP BY item
  ),
  order_final AS (
    SELECT
      o.item,
      CASE
        WHEN h.is_actual THEN COALESCE(a.actual_tubs, 0)
        ELSE COALESCE(e.order_tubs, 0)
      END AS order_tubs
    FROM oa o
    CROSS JOIN has_actuals h
    LEFT JOIN actuals a USING (item)
    LEFT JOIN est_selected e USING (item)
  ),
  reco AS (
    SELECT
      o.item,
      o.current_qty,
      o.avg_daily_usage,
      ROUND(o.on_hand_arrival, 2) AS on_hand_arrival,
      f.order_tubs,
      ROUND(o.on_hand_arrival + f.order_tubs, 2) AS post_restock_qty,
      ROUND(SAFE_DIVIDE(o.on_hand_arrival + f.order_tubs, NULLIF(o.avg_daily_usage, 0)), 1) AS post_restock_days_left,
      CASE
        WHEN o.item = 'Blade' THEN NULL
        ELSE f.order_tubs * (CASE WHEN o.item = 'Açaí' THEN 18 ELSE 20 END)
      END AS order_weight_lbs
    FROM oa o
    JOIN order_final f USING (item)
  ),
  combined AS (
    SELECT *, 0 AS _ord FROM reco
    UNION ALL
    SELECT
      'TOTAL',
      ROUND(SUM(current_qty), 2),
      ROUND(SUM(avg_daily_usage), 2),
      ROUND(SUM(on_hand_arrival), 2),
      SUM(order_tubs),
      ROUND(SUM(post_restock_qty), 2),
      ROUND(SAFE_DIVIDE(SUM(post_restock_qty), NULLIF(SUM(avg_daily_usage), 0)), 1),
      ROUND(SUM(order_weight_lbs) + 50 * CEIL(SAFE_DIVIDE(SUM(order_tubs), 40)), 0),
      1
    FROM reco
  )
  SELECT
    item AS Item,
    current_qty AS `Current Qty`,
    avg_daily_usage AS `Avg per day`,
    on_hand_arrival AS `On Hand at Restock`,
    CAST(ROUND(order_tubs) AS INT64) AS `Order Tubs`,
    order_weight_lbs AS `Order Weight lbs`,
    post_restock_qty AS `After Restock`,
    post_restock_days_left AS `Days Left After Restock`,
    _ord,
    dd.d1 AS delivery_date
  FROM combined
  CROSS JOIN dd
  ORDER BY _ord ASC, `Current Qty` DESC
);

CREATE OR REPLACE TABLE FUNCTION `jarvis-bhaga-prod.bhaga.tvf_order_reco_slot2`(
  max_tubs INT64
) AS (
  WITH
  dd AS (
    SELECT
      MAX(IF(slot = 1, delivery_date, NULL)) AS d1,
      MAX(IF(slot = 2, delivery_date, NULL)) AS d2
    FROM `jarvis-bhaga-prod.bhaga.vw_order_reco_next_dates`
  ),
  s1 AS (
    SELECT Item AS item, `Order Tubs` AS order1, `On Hand at Restock` AS on_hand_d1
    FROM `jarvis-bhaga-prod.bhaga.inventory_order_reco`
    WHERE store = 'palmetto' AND Item != 'TOTAL'
      AND (
        delivery_date = (SELECT d1 FROM dd)
        OR (delivery_date IS NULL AND Slot = 1)
      )
  ),
  oa AS (
    SELECT
      o.item, o.current_qty,
      COALESCE(o.avg_daily_usage, 0) AS avg_daily_usage,
      GREATEST(
        (s.on_hand_d1 + s.order1) - DATE_DIFF(dd.d2, dd.d1, DAY) * COALESCE(o.avg_daily_usage, 0),
        0
      ) AS on_hand_arrival
    FROM `jarvis-bhaga-prod.bhaga.vw_inventory_order_assistant` o
    JOIN s1 s USING (item)
    CROSS JOIN dd
    WHERE o.store = 'palmetto' AND dd.d2 IS NOT NULL
  ),
  actuals AS (
    SELECT item, SUM(quantity_tubs) AS actual_tubs
    FROM `jarvis-bhaga-prod.bhaga.inventory_restock_orders`, dd
    WHERE store = 'palmetto' AND delivery_date = dd.d2
    GROUP BY item
  ),
  has_actuals AS (SELECT COUNT(*) > 0 AS is_actual FROM actuals),
  budget AS (
    SELECT GREATEST(CAST(FLOOR(max_tubs - SUM(on_hand_arrival)) AS INT64), 0) AS tubs_budget
    FROM oa
  ),
  candidates AS (
    SELECT o.item, (o.on_hand_arrival + k - 1) / o.avg_daily_usage AS sort_key
    FROM oa o
    CROSS JOIN UNNEST(GENERATE_ARRAY(1, 300)) AS k
    CROSS JOIN budget b
    WHERE o.item != 'Blade' AND o.avg_daily_usage > 0 AND k <= b.tubs_budget
  ),
  ranked AS (
    SELECT item, ROW_NUMBER() OVER (ORDER BY sort_key ASC) AS rn FROM candidates
  ),
  est_selected AS (
    SELECT item, COUNT(*) AS order_tubs
    FROM ranked
    CROSS JOIN budget b
    WHERE rn <= b.tubs_budget
    GROUP BY item
  ),
  order_final AS (
    SELECT
      o.item,
      CASE
        WHEN h.is_actual THEN COALESCE(a.actual_tubs, 0)
        ELSE COALESCE(e.order_tubs, 0)
      END AS order_tubs
    FROM oa o
    CROSS JOIN has_actuals h
    LEFT JOIN actuals a USING (item)
    LEFT JOIN est_selected e USING (item)
  ),
  reco AS (
    SELECT
      o.item,
      o.current_qty,
      o.avg_daily_usage,
      ROUND(o.on_hand_arrival, 2) AS on_hand_arrival,
      f.order_tubs,
      ROUND(o.on_hand_arrival + f.order_tubs, 2) AS post_restock_qty,
      ROUND(SAFE_DIVIDE(o.on_hand_arrival + f.order_tubs, NULLIF(o.avg_daily_usage, 0)), 1) AS post_restock_days_left,
      CASE
        WHEN o.item = 'Blade' THEN NULL
        ELSE f.order_tubs * (CASE WHEN o.item = 'Açaí' THEN 18 ELSE 20 END)
      END AS order_weight_lbs
    FROM oa o
    JOIN order_final f USING (item)
  ),
  combined AS (
    SELECT *, 0 AS _ord FROM reco
    UNION ALL
    SELECT
      'TOTAL',
      ROUND(SUM(current_qty), 2),
      ROUND(SUM(avg_daily_usage), 2),
      ROUND(SUM(on_hand_arrival), 2),
      SUM(order_tubs),
      ROUND(SUM(post_restock_qty), 2),
      ROUND(SAFE_DIVIDE(SUM(post_restock_qty), NULLIF(SUM(avg_daily_usage), 0)), 1),
      ROUND(SUM(order_weight_lbs) + 50 * CEIL(SAFE_DIVIDE(SUM(order_tubs), 40)), 0),
      1
    FROM reco
  )
  SELECT
    item AS Item,
    current_qty AS `Current Qty`,
    avg_daily_usage AS `Avg per day`,
    on_hand_arrival AS `On Hand at Restock`,
    CAST(ROUND(order_tubs) AS INT64) AS `Order Tubs`,
    order_weight_lbs AS `Order Weight lbs`,
    post_restock_qty AS `After Restock`,
    post_restock_days_left AS `Days Left After Restock`,
    _ord,
    dd.d2 AS delivery_date
  FROM combined
  CROSS JOIN dd
  ORDER BY _ord ASC, `Current Qty` DESC
);

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_order_reco_combined` AS
WITH
dd AS (
  SELECT MAX(IF(slot=1,delivery_date,NULL)) AS d1,
         MAX(IF(slot=2,delivery_date,NULL)) AS d2
  FROM `jarvis-bhaga-prod.bhaga.vw_order_reco_next_dates`
),
src AS (
  SELECT
    dd.d1, dd.d2,
    (SELECT COUNT(*)>0 FROM `jarvis-bhaga-prod.bhaga.inventory_restock_orders` o
       WHERE o.store='palmetto' AND o.delivery_date=dd.d1) AS actual1,
    (SELECT COUNT(*)>0 FROM `jarvis-bhaga-prod.bhaga.inventory_restock_orders` o
       WHERE o.store='palmetto' AND o.delivery_date=dd.d2) AS actual2
  FROM dd
),
s1 AS (
  SELECT r.*
  FROM `jarvis-bhaga-prod.bhaga.inventory_order_reco` r
  CROSS JOIN dd
  WHERE r.store='palmetto'
    AND dd.d1 IS NOT NULL
    AND r.delivery_date = dd.d1
),
s2 AS (
  SELECT r.*
  FROM `jarvis-bhaga-prod.bhaga.inventory_order_reco` r
  CROSS JOIN dd
  WHERE r.store='palmetto'
    AND dd.d2 IS NOT NULL
    AND r.delivery_date = dd.d2
)
SELECT
  COALESCE(s1.Item, s2.Item) AS Item,
  COALESCE(s1.`Current Qty`, s2.`Current Qty`) AS `Current Qty`,
  COALESCE(s1.`Avg per day`, s2.`Avg per day`) AS `Avg per day`,
  s1.`On Hand at Restock` AS `On Hand 1`, s1.`Order Tubs` AS `Order Tubs 1`,
  s1.`Order Weight lbs` AS `Order Weight 1`, s1.`After Restock` AS `After Restock 1`,
  s1.`Days Left After Restock` AS `Days Left 1`,
  IF(src.d1 IS NULL, NULL, IF(src.actual1,'Actuals','Estimated')) AS `Source 1`,
  s2.`On Hand at Restock` AS `On Hand 2`, s2.`Order Tubs` AS `Order Tubs 2`,
  s2.`Order Weight lbs` AS `Order Weight 2`, s2.`After Restock` AS `After Restock 2`,
  s2.`Days Left After Restock` AS `Days Left 2`,
  IF(src.d2 IS NULL, NULL, IF(src.actual2,'Actuals','Estimated')) AS `Source 2`,
  COALESCE(s1._ord, s2._ord) AS _ord,
  CAST(COALESCE(s1.refreshed_at, s2.refreshed_at) AS STRING) AS refresh_date
FROM s1 FULL OUTER JOIN s2 ON s1.Item = s2.Item
CROSS JOIN src
ORDER BY _ord ASC, `Current Qty` DESC;
