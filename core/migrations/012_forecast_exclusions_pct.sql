-- 012_forecast_exclusions_pct.sql
-- Enrich the Forecast Inputs / Exclusions view with a prior-same-weekday
-- comparison so the operator can spot week-over-week swings and decide which
-- days to exclude from the forecast seed.
-- Applied via: python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

-- Each row now carries, alongside the day's own orders/items:
--   prev_wk_orders / prev_wk_items     — the SAME weekday one week earlier
--   orders_vs_prev_wk / items_vs_prev_wk — signed % change vs that day
-- A large swing on a day is the signal for an exclusion suggestion.
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
  cur.forecast_exclude,
  cur.outlier_reason,
  cur.forecast_exclude_reason
FROM `jarvis-bhaga-prod.bhaga.model_labor_daily` cur
LEFT JOIN `jarvis-bhaga-prod.bhaga.model_labor_daily` prev
  ON prev.date = DATE_SUB(cur.date, INTERVAL 7 DAY)
WHERE cur.date >= DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL 60 DAY);
