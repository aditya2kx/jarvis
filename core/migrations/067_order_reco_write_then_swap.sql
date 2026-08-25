-- 067_order_reco_write_then_swap.sql
-- Issue #261 (067): slot_n chains from the latest refreshed_at per item so
-- write-then-swap (INSERT new generation, then DELETE old) does not
-- cartesian-join old+new slot N-1 rows.

CREATE OR REPLACE TABLE FUNCTION `jarvis-bhaga-prod.bhaga.tvf_order_reco_slot_n`(
  max_tubs INT64,
  target_slot INT64
) AS (
  WITH
  dd AS (
    SELECT
      MAX(IF(slot = target_slot - 1, delivery_date, NULL)) AS d_prev,
      MAX(IF(slot = target_slot, delivery_date, NULL)) AS d_cur
    FROM `jarvis-bhaga-prod.bhaga.vw_order_reco_next_dates`
  ),
  s_prev AS (
    SELECT
      Item AS item,
      `Order Tubs` AS order_prev,
      `On Hand at Restock` AS on_hand_prev
    FROM `jarvis-bhaga-prod.bhaga.inventory_order_reco`
    WHERE store = 'palmetto' AND Item != 'TOTAL'
      AND (
        delivery_date = (SELECT d_prev FROM dd)
        OR (delivery_date IS NULL AND Slot = target_slot - 1)
      )
    QUALIFY ROW_NUMBER() OVER (PARTITION BY Item ORDER BY refreshed_at DESC) = 1
  ),
  oa AS (
    SELECT
      o.item, o.current_qty,
      COALESCE(o.avg_daily_usage, 0) AS avg_daily_usage,
      GREATEST(
        (s.on_hand_prev + s.order_prev)
          - DATE_DIFF(dd.d_cur, dd.d_prev, DAY) * COALESCE(o.avg_daily_usage, 0),
        0
      ) AS on_hand_arrival
    FROM `jarvis-bhaga-prod.bhaga.vw_inventory_order_assistant` o
    JOIN s_prev s USING (item)
    CROSS JOIN dd
    WHERE o.store = 'palmetto' AND dd.d_cur IS NOT NULL AND dd.d_prev IS NOT NULL
  ),
  actuals AS (
    SELECT item, SUM(quantity_tubs) AS actual_tubs
    FROM `jarvis-bhaga-prod.bhaga.inventory_restock_orders`, dd
    WHERE store = 'palmetto' AND delivery_date = dd.d_cur
    GROUP BY item
  ),
  has_actuals AS (SELECT COUNT(*) > 0 AS is_actual FROM actuals),
  overrides AS (
    SELECT item, SUM(quantity_tubs) AS override_tubs
    FROM `jarvis-bhaga-prod.bhaga.inventory_order_tub_overrides`, dd
    WHERE store = 'palmetto' AND delivery_date = dd.d_cur
    GROUP BY item
  ),
  locked_budget AS (
    SELECT COALESCE(SUM(override_tubs), 0) AS locked_tubs FROM overrides
  ),
  budget AS (
    SELECT GREATEST(
      CAST(FLOOR(max_tubs - SUM(o.on_hand_arrival)) AS INT64)
        - CAST((SELECT locked_tubs FROM locked_budget) AS INT64),
      0
    ) AS tubs_budget
    FROM oa o
  ),
  candidates AS (
    SELECT o.item, (o.on_hand_arrival + k - 1) / o.avg_daily_usage AS sort_key
    FROM oa o
    CROSS JOIN UNNEST(GENERATE_ARRAY(1, 300)) AS k
    CROSS JOIN budget b
    LEFT JOIN overrides ov ON ov.item = o.item
    WHERE o.item != 'Blade'
      AND o.avg_daily_usage > 0
      AND k <= b.tubs_budget
      AND ov.item IS NULL
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
        WHEN ov.item IS NOT NULL THEN ov.override_tubs
        ELSE COALESCE(e.order_tubs, 0)
      END AS order_tubs
    FROM oa o
    CROSS JOIN has_actuals h
    LEFT JOIN actuals a USING (item)
    LEFT JOIN overrides ov USING (item)
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
      ROUND(SAFE_DIVIDE(o.on_hand_arrival + f.order_tubs, NULLIF(o.avg_daily_usage, 0)), 1)
        AS post_restock_days_left,
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
    dd.d_cur AS delivery_date
  FROM combined
  CROSS JOIN dd
  ORDER BY _ord ASC, `Current Qty` DESC
);
