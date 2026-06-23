-- Migration 022: ramp-aware forecast table + parallel views for Section 7A.
--
-- Creates model_forecast_ramp_daily (parallel to model_forecast_daily) and
-- two views that mirror the existing Section 7 views exactly, pointing at the
-- ramp table instead of the heuristic table.  Fully additive — no existing
-- table or view is touched.
--
-- Applied via: python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

-- Table: ramp-aware forecast rows, one per future date.
-- merge_keys (runtime): ["date"]
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.model_forecast_ramp_daily` (
  date                    DATE      NOT NULL,
  forecast_orders         INT64,
  forecast_items          FLOAT64,
  forecast_generated_at   STRING,
  forecast_model_version  STRING,
  materialized_at_utc     TIMESTAMP
);

-- Forward-looking view: next 30 days with prior-week COALESCE(actual, ramp forecast).
-- Mirrors vw_model_forecast (migration 015) pointing at model_forecast_ramp_daily.
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_model_forecast_ramp` AS
SELECT
  f.date,
  FORMAT_DATE('%a', f.date)                                              AS dow,
  f.forecast_orders,
  f.forecast_items,
  COALESCE(IF(a.orders > 0, a.orders,     NULL), pf.forecast_orders)   AS prior_wk_orders,
  COALESCE(IF(a.orders > 0, a.items_sold, NULL), pf.forecast_items)    AS prior_wk_items,
  SAFE_DIVIDE(
    f.forecast_orders - COALESCE(IF(a.orders > 0, a.orders, NULL), pf.forecast_orders),
    NULLIF(COALESCE(IF(a.orders > 0, a.orders, NULL), pf.forecast_orders), 0)
  )                                                                      AS orders_vs_prior_wk,
  SAFE_DIVIDE(
    f.forecast_items - COALESCE(IF(a.orders > 0, a.items_sold, NULL), pf.forecast_items),
    NULLIF(COALESCE(IF(a.orders > 0, a.items_sold, NULL), pf.forecast_items), 0)
  )                                                                      AS items_vs_prior_wk,
  s.scheduled_hours
FROM `jarvis-bhaga-prod.bhaga.model_forecast_ramp_daily` f
LEFT JOIN `jarvis-bhaga-prod.bhaga.vw_model_labor_daily` a
  ON a.date = DATE_SUB(f.date, INTERVAL 7 DAY)
LEFT JOIN `jarvis-bhaga-prod.bhaga.model_forecast_ramp_daily` pf
  ON pf.date = DATE_SUB(f.date, INTERVAL 7 DAY)
LEFT JOIN `jarvis-bhaga-prod.bhaga.adp_scheduled_daily` s
  ON s.date = f.date
WHERE f.date >= CURRENT_DATE('America/Chicago');

-- Accuracy view: ramp-forecast rows that now have actuals.
-- Mirrors vw_forecast_accuracy (migration 011) pointing at ramp table.
-- Excludes forecast_exclude days so the accuracy chart isn't distorted by
-- anomaly days — matching the same guardrail used in the backtest.
-- Uses LEFT JOIN instead of a correlated subquery (BigQuery limitation).
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_forecast_ramp_accuracy` AS
SELECT
  f.date,
  FORMAT_DATE('%a', f.date)  AS dow,
  f.forecast_orders,
  a.orders                   AS actual_orders,
  f.forecast_items,
  a.items_sold               AS actual_items,
  f.forecast_model_version
FROM `jarvis-bhaga-prod.bhaga.model_forecast_ramp_daily` f
JOIN `jarvis-bhaga-prod.bhaga.vw_model_labor_daily` a
  ON a.date = f.date
LEFT JOIN `jarvis-bhaga-prod.bhaga.model_labor_daily` excl
  ON excl.date = f.date
WHERE a.orders > 0
  AND NOT COALESCE(excl.forecast_exclude, FALSE);
