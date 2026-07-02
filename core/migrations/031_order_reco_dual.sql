-- 031_order_reco_dual.sql
-- Issue #137: replace the single hardcoded 10-day-lead-time recommendation
-- (migration 029's tvf_order_reco) with two calendar-date-driven, chained
-- recommendations, sourced from the operator-registered restock dates
-- (migration 030's inventory_restock_schedule / inventory_restock_orders).
--
-- Option D — MATERIALIZED table, not a live chained TVF. Verified against
-- prod BigQuery: computing both slots' water-fill CTEs at the same query
-- level (or nesting slot 2's TVF call inside slot 1's, or a single flat
-- tvf_order_reco_dual, or window-function totals, or GROUP BY ROLLUP) all
-- fail with `400 Resources exceeded ... query is too complex` once slot 2
-- has to re-derive slot 1's entire GENERATE_ARRAY water-fill chain PLUS its
-- own. A single slot (tvf_order_reco_slot1 alone) plans fine -- the
-- complexity comes specifically from re-inlining a second copy of the
-- water-fill sub-tree in the same query.
--
-- The fix: each slot is computed by a SEPARATE table function call (a
-- separate BQ job), and the results are written into a physical table,
-- inventory_order_reco. Slot 2 reads slot 1's OUTPUT from that materialized
-- table (a cheap flat table scan) instead of calling slot 1's TVF or
-- re-deriving its CTEs -- so slot 2's query plan stays exactly as small as
-- slot 1's. See core/order_reco.py for the refresh sequence (DELETE, then
-- INSERT slot 1, then INSERT slot 2 -- slot 1 must land before slot 2 runs).
--
-- vw_order_reco_next_dates
--   The next two FUTURE (>= today, America/Chicago) distinct delivery dates
--   registered for the store, oldest first (slot 1, slot 2). Past dates are
--   silently dropped -- the operator never has to "clean up" an elapsed date.
--
-- inventory_order_reco
--   Materialized output: one row per (store, Slot, Item), plus a TOTAL row
--   per slot. Refreshed by core/order_reco.py's refresh_order_reco(), never
--   written to directly by Grafana or the handler.
--
-- tvf_order_reco_slot1(max_tubs) / tvf_order_reco_slot2(max_tubs)
--   Same max-min water-fill as migration 029's tvf_order_reco, but ship_days
--   is derived from each slot's real calendar date (vw_order_reco_next_dates)
--   instead of a fixed $oa_ship_days variable. If actual order quantities
--   have been uploaded for a slot's date, that slot shows those actuals
--   instead of the estimated water-fill allocation -- the table always
--   reflects "what will really arrive", estimated until it's known. Slot 2's
--   on-hand-at-arrival reads slot 1's MATERIALIZED row (`inventory_order_reco
--   WHERE Slot = 1`), so it reflects slot 1's current estimated-or-actual
--   allocation without re-deriving it. Zero slot-2 rows when there is no
--   second future date registered (`dd.d2 IS NOT NULL` guard).
--
-- vw_order_reco_slot1 / vw_order_reco_slot2
--   Thin pass-through views over inventory_order_reco (Grafana panels 81/82
--   read these, never the table or TVFs directly -- keeps
--   scripts/check_grafana_no_logic.py's pass-through guarantee). Expose a
--   `refresh_date` string column (CAST of refreshed_at) so
--   agents/bhaga/scripts/status.py can track freshness on these vw_* names
--   like every other Grafana view -- the panel rawSql EXCEPTs it (still a
--   clean `SELECT ... FROM vw_*` pass-through, no logic added).
--
-- Migration 029's tvf_order_reco/vw_order_assistant_table are left in place
-- (superseded but harmless reference fallback).

DROP TABLE FUNCTION IF EXISTS `jarvis-bhaga-prod.bhaga.tvf_order_reco_dual`;

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.inventory_order_reco` (
  store STRING NOT NULL,
  Slot INT64 NOT NULL,
  Item STRING,
  `Current Qty` FLOAT64,
  `Avg per day` FLOAT64,
  `On Hand at Restock` FLOAT64,
  `Order Tubs` INT64,
  `Order Weight lbs` FLOAT64,
  `After Restock` FLOAT64,
  `Days Left After Restock` FLOAT64,
  _ord INT64,
  refreshed_at TIMESTAMP
);

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
      AND delivery_date >= CURRENT_DATE('America/Chicago')
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
    -- Fixed literal array bound (300 comfortably exceeds any realistic
    -- max_tubs) + CROSS JOIN to budget (not a correlated scalar subquery)
    -- keeps the plan flat -- see module comment re: query-planning limits.
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
  -- Actual-vs-estimated override: if the operator has uploaded actuals for
  -- this date, use those verbatim (0 for any item not present in the
  -- upload); otherwise fall back to the estimated water-fill allocation.
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
    -- order_tubs is FLOAT64 here because the actuals branch (SUM of
    -- inventory_restock_orders.quantity_tubs, FLOAT64) and the estimated
    -- branch (COUNT(*), INT64) share one CASE expression, which BQ widens
    -- to FLOAT64. inventory_order_reco.`Order Tubs` is INT64, so cast here.
    CAST(ROUND(order_tubs) AS INT64) AS `Order Tubs`,
    order_weight_lbs AS `Order Weight lbs`,
    post_restock_qty AS `After Restock`,
    post_restock_days_left AS `Days Left After Restock`,
    _ord
  FROM combined
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
  -- Chaining fix (Option D): read slot 1's MATERIALIZED output, not a nested
  -- tvf_order_reco_slot1(...) call and not a re-derivation of its CTEs --
  -- see module comment. This is a flat table scan, so slot 2's plan stays
  -- exactly as small as slot 1's.
  s1 AS (
    SELECT Item AS item, `Order Tubs` AS order1, `On Hand at Restock` AS on_hand_d1
    FROM `jarvis-bhaga-prod.bhaga.inventory_order_reco`
    WHERE store = 'palmetto' AND Slot = 1 AND Item != 'TOTAL'
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
    _ord
  FROM combined
  ORDER BY _ord ASC, `Current Qty` DESC
);

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_order_reco_slot1` AS
SELECT *, CAST(refreshed_at AS STRING) AS refresh_date
FROM `jarvis-bhaga-prod.bhaga.inventory_order_reco`
WHERE store = 'palmetto' AND Slot = 1
ORDER BY _ord ASC, `Current Qty` DESC;

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_order_reco_slot2` AS
SELECT *, CAST(refreshed_at AS STRING) AS refresh_date
FROM `jarvis-bhaga-prod.bhaga.inventory_order_reco`
WHERE store = 'palmetto' AND Slot = 2
ORDER BY _ord ASC, `Current Qty` DESC;
