-- 015_forecast_view_dow_fallback.sql
-- Refresh vw_model_forecast: add dow (Mon/Tue/...) and make the prior-week
-- comparison treat a failed/closed prior day (orders=0) as MISSING so it falls
-- back to that day's forecast (e.g. today, 6/10). Keeps scheduled_hours + joins.
-- Applied via: python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_model_forecast` AS
SELECT
  f.date,
  FORMAT_DATE('%a', f.date) AS dow,
  f.forecast_orders,
  f.forecast_items,
  COALESCE(IF(a.orders > 0, a.orders,     NULL), pf.forecast_orders) AS prior_wk_orders,
  COALESCE(IF(a.orders > 0, a.items_sold, NULL), pf.forecast_items)  AS prior_wk_items,
  SAFE_DIVIDE(
    f.forecast_orders - COALESCE(IF(a.orders > 0, a.orders, NULL), pf.forecast_orders),
    NULLIF(COALESCE(IF(a.orders > 0, a.orders, NULL), pf.forecast_orders), 0)
  )                                                                  AS orders_vs_prior_wk,
  SAFE_DIVIDE(
    f.forecast_items - COALESCE(IF(a.orders > 0, a.items_sold, NULL), pf.forecast_items),
    NULLIF(COALESCE(IF(a.orders > 0, a.items_sold, NULL), pf.forecast_items), 0)
  )                                                                  AS items_vs_prior_wk,
  s.scheduled_hours
FROM `jarvis-bhaga-prod.bhaga.model_forecast_daily` f
LEFT JOIN `jarvis-bhaga-prod.bhaga.vw_model_labor_daily` a
  ON a.date = DATE_SUB(f.date, INTERVAL 7 DAY)
LEFT JOIN `jarvis-bhaga-prod.bhaga.model_forecast_daily` pf
  ON pf.date = DATE_SUB(f.date, INTERVAL 7 DAY)
LEFT JOIN `jarvis-bhaga-prod.bhaga.adp_scheduled_daily` s
  ON s.date = f.date
WHERE f.date >= CURRENT_DATE('America/Chicago');
