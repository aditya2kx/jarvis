-- 011_labor_forecast.sql
-- BQ-authoritative daily forecast (orders + items) and the views Grafana reads.
-- Applied via: python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

-- Table: slim forecast rows, one per future date.
-- loader only ever writes today+1..today+30 (future window), so past dates
-- freeze at their last 1-day-ahead value — implicit forecast accuracy.
-- merge_keys (runtime): ["date"]
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.model_forecast_daily` (
  date                    DATE NOT NULL,
  forecast_orders         INT64,
  forecast_items          FLOAT64,
  forecast_generated_at   STRING,
  materialized_at_utc     TIMESTAMP
);

-- Forward-looking view: next 30 days with prior-week COALESCE(actual, forecast).
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
  )                                              AS items_vs_prior_wk
FROM `jarvis-bhaga-prod.bhaga.model_forecast_daily` f
LEFT JOIN `jarvis-bhaga-prod.bhaga.vw_model_labor_daily` a
  ON a.date = DATE_SUB(f.date, INTERVAL 7 DAY)
LEFT JOIN `jarvis-bhaga-prod.bhaga.model_forecast_daily` pf
  ON pf.date = DATE_SUB(f.date, INTERVAL 7 DAY)
WHERE f.date >= CURRENT_DATE('America/Chicago');

-- Accuracy view: forecast days that now have actuals (same date join).
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_forecast_accuracy` AS
SELECT
  f.date,
  f.forecast_orders,
  a.orders       AS actual_orders,
  f.forecast_items,
  a.items_sold   AS actual_items
FROM `jarvis-bhaga-prod.bhaga.model_forecast_daily` f
JOIN `jarvis-bhaga-prod.bhaga.vw_model_labor_daily` a
  ON a.date = f.date;

-- Exclusions view: recent input days the forecaster saw; shows which were
-- dropped (forecast_exclude=TRUE). Reads the TABLE directly because
-- vw_model_labor_daily does not expose outlier_reason / forecast_exclude_reason.
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_forecast_exclusions` AS
SELECT
  date,
  dow,
  orders,
  forecast_exclude,
  outlier_reason,
  forecast_exclude_reason
FROM `jarvis-bhaga-prod.bhaga.model_labor_daily`
WHERE date >= DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL 60 DAY);
