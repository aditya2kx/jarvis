-- 029_order_assistant_functions.sql
-- Issue #126: move Order Assistant panel logic out of Grafana into BigQuery.
--
-- Grafana panels 79 and 81 used to carry the entire analytics-table TOTAL-row
-- synthesis and the order-recommendation water-fill algorithm as inline
-- rawSql. This migration ports both, unchanged, into BigQuery so Grafana
-- becomes a pure `SELECT * FROM ...` pass-through (see
-- scripts/check_grafana_no_logic.py, which enforces this going forward).
--
-- vw_order_assistant_table  -- was panel 79's rawSql body
--   Reads vw_inventory_order_assistant and appends a TOTAL row via UNION ALL,
--   identical column-for-column to the panel it replaces.
--
-- tvf_order_reco(ship_days, max_tubs) -- was panel 81's rawSql body
--   Max-min water-fill order recommendation, parameterized by the two Grafana
--   dashboard variables ($oa_ship_days default 10, $oa_max_tubs default 120)
--   so the operator's live sliders keep working -- only the algorithm moved,
--   not its interactivity. Blade is reserved against the cap but never
--   ordered (see .cursor/rules/bhaga.mdc "Order Assistant recommendation").

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_order_assistant_table` AS
WITH b AS (
  SELECT
    item, current_qty, reported,
    CAST(last_restock_date AS STRING) AS last_restock,
    usage_7d_total, avg_daily_usage, days_left, days_considered, excluded_days
  FROM `jarvis-bhaga-prod.bhaga.vw_inventory_order_assistant`
  WHERE store = 'palmetto'
),
combined AS (
  SELECT *, 0 AS _ord FROM b
  UNION ALL
  SELECT
    'TOTAL',
    ROUND(SUM(current_qty), 2),
    CAST(NULL AS STRING),
    CAST(NULL AS STRING),
    ROUND(SUM(usage_7d_total), 2),
    ROUND(SUM(usage_7d_total) / 7, 2),
    ROUND(SAFE_DIVIDE(SUM(current_qty), NULLIF(SUM(usage_7d_total) / 7, 0)), 1),
    CAST(NULL AS STRING),
    CAST(NULL AS STRING),
    1
  FROM b
)
SELECT
  item AS Item,
  current_qty AS `Current Qty`,
  reported AS Reported,
  last_restock AS `Last Restock`,
  usage_7d_total AS `Usage 7d`,
  avg_daily_usage AS `Avg per day`,
  days_left AS `Days Left`,
  days_considered AS `Days Considered`,
  excluded_days AS Exclusions
FROM combined
ORDER BY _ord ASC, current_qty DESC;

CREATE OR REPLACE TABLE FUNCTION `jarvis-bhaga-prod.bhaga.tvf_order_reco`(
  ship_days INT64, max_tubs INT64
) AS (
  WITH oa AS (
    SELECT
      item, current_qty,
      COALESCE(avg_daily_usage, 0) AS avg_daily_usage,
      GREATEST(current_qty - ship_days * COALESCE(avg_daily_usage, 0), 0) AS on_hand_arrival
    FROM `jarvis-bhaga-prod.bhaga.vw_inventory_order_assistant`
    WHERE store = 'palmetto'
  ),
  budget AS (
    SELECT GREATEST(CAST(FLOOR(max_tubs - SUM(on_hand_arrival)) AS INT64), 0) AS tubs_budget
    FROM oa
  ),
  candidates AS (
    SELECT
      o.item,
      (o.on_hand_arrival + k - 1) / o.avg_daily_usage AS sort_key
    FROM oa o
    CROSS JOIN UNNEST(GENERATE_ARRAY(1, GREATEST((SELECT tubs_budget FROM budget), 1))) AS k
    WHERE o.item != 'Blade' AND o.avg_daily_usage > 0
  ),
  ranked AS (
    SELECT item, ROW_NUMBER() OVER (ORDER BY sort_key ASC) AS rn
    FROM candidates
  ),
  selected AS (
    SELECT item, COUNT(*) AS order_tubs
    FROM ranked
    WHERE rn <= (SELECT tubs_budget FROM budget)
    GROUP BY item
  ),
  reco AS (
    SELECT
      o.item,
      o.current_qty,
      o.avg_daily_usage,
      ROUND(o.on_hand_arrival, 2) AS on_hand_arrival,
      COALESCE(s.order_tubs, 0) AS order_tubs,
      ROUND(o.on_hand_arrival + COALESCE(s.order_tubs, 0), 2) AS post_restock_qty,
      ROUND(SAFE_DIVIDE(o.on_hand_arrival + COALESCE(s.order_tubs, 0), NULLIF(o.avg_daily_usage, 0)), 1) AS post_restock_days_left,
      CASE
        WHEN o.item = 'Blade' THEN NULL
        ELSE COALESCE(s.order_tubs, 0) * (CASE WHEN o.item = 'Açaí' THEN 18 ELSE 20 END)
      END AS order_weight_lbs
    FROM oa o
    LEFT JOIN selected s USING (item)
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
    on_hand_arrival AS `On Hand in 10d`,
    order_tubs AS `Order Tubs`,
    order_weight_lbs AS `Order Weight lbs`,
    post_restock_qty AS `After Restock`,
    post_restock_days_left AS `Days Left After Restock`
  FROM combined
  ORDER BY _ord ASC, current_qty DESC
);
