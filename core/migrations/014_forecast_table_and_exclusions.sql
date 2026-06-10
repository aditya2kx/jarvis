-- 014_forecast_table_and_exclusions.sql
-- Three idempotent changes:
--   1. Add forecast_model_version column to model_forecast_daily so each row
--      records which model version produced it (for vw_forecast_accuracy audits).
--   2. Refresh vw_model_forecast to expose ADP scheduled_hours per date.
--   3. Refresh vw_forecast_exclusions to expose net_sales + AOV vs prev week
--      so promo / comped days (low AOV) are visible alongside order volumes.
-- Applied via: python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

-- 1. forecast_model_version column
ALTER TABLE `jarvis-bhaga-prod.bhaga.model_forecast_daily`
  ADD COLUMN IF NOT EXISTS forecast_model_version STRING;

-- 2. vw_model_forecast — forward 30-day view + prior-week comparison + scheduled hours.
--    Replaces the 011 definition; adds LEFT JOIN adp_scheduled_daily for scheduled_hours.
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_model_forecast` AS
SELECT
  f.date,
  f.forecast_orders,
  f.forecast_items,
  COALESCE(a.orders,      pf.forecast_orders)   AS prior_wk_orders,
  COALESCE(a.items_sold,  pf.forecast_items)    AS prior_wk_items,
  SAFE_DIVIDE(
    f.forecast_orders - COALESCE(a.orders, pf.forecast_orders),
    NULLIF(COALESCE(a.orders, pf.forecast_orders), 0)
  )                                              AS orders_vs_prior_wk,
  SAFE_DIVIDE(
    f.forecast_items - COALESCE(a.items_sold, pf.forecast_items),
    NULLIF(COALESCE(a.items_sold, pf.forecast_items), 0)
  )                                              AS items_vs_prior_wk,
  s.scheduled_hours
FROM `jarvis-bhaga-prod.bhaga.model_forecast_daily` f
LEFT JOIN `jarvis-bhaga-prod.bhaga.vw_model_labor_daily` a
  ON a.date = DATE_SUB(f.date, INTERVAL 7 DAY)
LEFT JOIN `jarvis-bhaga-prod.bhaga.model_forecast_daily` pf
  ON pf.date = DATE_SUB(f.date, INTERVAL 7 DAY)
LEFT JOIN `jarvis-bhaga-prod.bhaga.adp_scheduled_daily` s
  ON s.date = f.date
WHERE f.date >= CURRENT_DATE('America/Chicago');

-- 3. vw_forecast_exclusions — recent input days with net_sales + AOV context.
--    Replaces the 012 definition; adds net_sales/AOV columns to spot comped days.
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_forecast_exclusions` AS
SELECT
  cur.date,
  cur.dow,
  cur.orders,
  cur.items_sold,
  prev.orders                                                    AS prev_wk_orders,
  prev.items_sold                                                AS prev_wk_items,
  SAFE_DIVIDE(cur.orders     - prev.orders,     NULLIF(prev.orders, 0))      AS orders_vs_prev_wk,
  SAFE_DIVIDE(cur.items_sold - prev.items_sold, NULLIF(prev.items_sold, 0))  AS items_vs_prev_wk,
  cur.net_sales,
  prev.net_sales                                                 AS prev_wk_net_sales,
  SAFE_DIVIDE(cur.net_sales - prev.net_sales, NULLIF(prev.net_sales, 0))     AS net_sales_vs_prev_wk,
  SAFE_DIVIDE(cur.net_sales, NULLIF(cur.orders, 0))              AS aov,
  SAFE_DIVIDE(prev.net_sales, NULLIF(prev.orders, 0))            AS prev_wk_aov,
  cur.forecast_exclude,
  cur.outlier_reason,
  cur.forecast_exclude_reason
FROM `jarvis-bhaga-prod.bhaga.model_labor_daily` cur
LEFT JOIN `jarvis-bhaga-prod.bhaga.model_labor_daily` prev
  ON prev.date = DATE_SUB(cur.date, INTERVAL 7 DAY)
WHERE cur.date >= DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL 60 DAY);
