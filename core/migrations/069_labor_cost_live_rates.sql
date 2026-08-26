-- 069_labor_cost_live_rates.sql
-- Issue #267: labor $ / % for console + Grafana from current adp_wage_rates ×
-- adp_shifts, not frozen model_labor_daily dollars (pay_info $1.25 scrape left
-- weekly PT % at 12.8/17.8 until FORCE_MODEL_RECOMPUTE even after rates restored).
-- Sales/orders/items stay on model_labor_daily. FT bucket = salaried OR
-- excluded_from_labor_pct (same as hour-grain / live-labor-cost.ts).
-- No view-on-view: weekly duplicates the daily join.
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c
--   "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_labor_daily_live` AS
WITH live_labor AS (
  SELECT
    s.date,
    SUM(IF(
      IFNULL(w.is_salaried, FALSE) OR IFNULL(w.excluded_from_labor_pct, FALSE),
      0,
      s.total_hours
    )) AS hourly_hours,
    SUM(IF(
      IFNULL(w.is_salaried, FALSE) OR IFNULL(w.excluded_from_labor_pct, FALSE),
      s.total_hours,
      0
    )) AS fulltime_hours,
    SUM(IF(
      IFNULL(w.is_salaried, FALSE) OR IFNULL(w.excluded_from_labor_pct, FALSE),
      0,
      s.total_hours * IFNULL(w.wage_rate_dollars, 0)
    )) AS hourly_labor_cost,
    SUM(IF(
      IFNULL(w.is_salaried, FALSE) OR IFNULL(w.excluded_from_labor_pct, FALSE),
      s.total_hours * IFNULL(w.wage_rate_dollars, 0),
      0
    )) AS fulltime_labor_cost
  FROM `jarvis-bhaga-prod.bhaga.adp_shifts` s
  LEFT JOIN `jarvis-bhaga-prod.bhaga.adp_wage_rates` w
    ON w.employee_id = s.employee_id
  WHERE IFNULL(s.total_hours, 0) > 0
  GROUP BY s.date
)
SELECT
  COALESCE(m.date, l.date) AS date,
  m.dow,
  COALESCE(m.net_sales, 0) AS net_sales,
  COALESCE(m.orders, 0) AS orders,
  COALESCE(m.items_sold, 0) AS items_sold,
  COALESCE(l.hourly_hours, 0) AS hourly_hours,
  COALESCE(l.fulltime_hours, 0) AS fulltime_hours,
  COALESCE(l.hourly_hours, 0) + COALESCE(l.fulltime_hours, 0) AS total_hours,
  COALESCE(l.hourly_labor_cost, 0) AS hourly_labor_cost,
  COALESCE(l.fulltime_labor_cost, 0) AS fulltime_labor_cost,
  COALESCE(l.hourly_labor_cost, 0) + COALESCE(l.fulltime_labor_cost, 0) AS total_labor_cost,
  SAFE_DIVIDE(
    COALESCE(l.hourly_labor_cost, 0) + COALESCE(l.fulltime_labor_cost, 0),
    NULLIF(m.net_sales, 0)
  ) AS labor_pct,
  SAFE_DIVIDE(COALESCE(l.hourly_labor_cost, 0), NULLIF(m.net_sales, 0)) AS hourly_pct,
  SAFE_DIVIDE(COALESCE(l.fulltime_labor_cost, 0), NULLIF(m.net_sales, 0)) AS fulltime_pct,
  SAFE_DIVIDE(
    COALESCE(l.hourly_hours, 0) + COALESCE(l.fulltime_hours, 0),
    NULLIF(m.items_sold, 0)
  ) AS hours_per_item,
  SAFE_DIVIDE(COALESCE(l.hourly_hours, 0), NULLIF(m.items_sold, 0)) AS hourly_hours_per_item,
  SAFE_DIVIDE(COALESCE(l.fulltime_hours, 0), NULLIF(m.items_sold, 0)) AS fulltime_hours_per_item,
  SAFE_DIVIDE(m.net_sales, NULLIF(m.orders, 0)) AS avg_order_price
FROM `jarvis-bhaga-prod.bhaga.model_labor_daily` m
FULL OUTER JOIN live_labor l
  ON m.date = l.date;

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_labor_weekly_live` AS
WITH live_labor AS (
  SELECT
    s.date,
    SUM(IF(
      IFNULL(w.is_salaried, FALSE) OR IFNULL(w.excluded_from_labor_pct, FALSE),
      0,
      s.total_hours
    )) AS hourly_hours,
    SUM(IF(
      IFNULL(w.is_salaried, FALSE) OR IFNULL(w.excluded_from_labor_pct, FALSE),
      s.total_hours,
      0
    )) AS fulltime_hours,
    SUM(IF(
      IFNULL(w.is_salaried, FALSE) OR IFNULL(w.excluded_from_labor_pct, FALSE),
      0,
      s.total_hours * IFNULL(w.wage_rate_dollars, 0)
    )) AS hourly_labor_cost,
    SUM(IF(
      IFNULL(w.is_salaried, FALSE) OR IFNULL(w.excluded_from_labor_pct, FALSE),
      s.total_hours * IFNULL(w.wage_rate_dollars, 0),
      0
    )) AS fulltime_labor_cost
  FROM `jarvis-bhaga-prod.bhaga.adp_shifts` s
  LEFT JOIN `jarvis-bhaga-prod.bhaga.adp_wage_rates` w
    ON w.employee_id = s.employee_id
  WHERE IFNULL(s.total_hours, 0) > 0
  GROUP BY s.date
),
daily AS (
  SELECT
    COALESCE(m.date, l.date) AS date,
    COALESCE(m.net_sales, 0) AS net_sales,
    COALESCE(m.orders, 0) AS orders,
    COALESCE(m.items_sold, 0) AS items_sold,
    COALESCE(l.hourly_hours, 0) AS hourly_hours,
    COALESCE(l.fulltime_hours, 0) AS fulltime_hours,
    COALESCE(l.hourly_labor_cost, 0) AS hourly_labor_cost,
    COALESCE(l.fulltime_labor_cost, 0) AS fulltime_labor_cost
  FROM `jarvis-bhaga-prod.bhaga.model_labor_daily` m
  FULL OUTER JOIN live_labor l
    ON m.date = l.date
)
SELECT
  DATE_TRUNC(date, WEEK(MONDAY)) AS week_start,
  DATE_ADD(DATE_TRUNC(date, WEEK(MONDAY)), INTERVAL 6 DAY) AS week_end,
  FORMAT_DATE('%G-W%V', DATE_TRUNC(date, WEEK(MONDAY))) AS iso_week,
  SUM(net_sales) AS net_sales,
  SUM(orders) AS orders,
  SUM(items_sold) AS items_sold,
  SUM(hourly_hours) AS hourly_hours,
  SUM(fulltime_hours) AS fulltime_hours,
  SUM(hourly_hours) + SUM(fulltime_hours) AS total_hours,
  SUM(hourly_labor_cost) AS hourly_labor_cost,
  SUM(fulltime_labor_cost) AS fulltime_labor_cost,
  SUM(hourly_labor_cost) + SUM(fulltime_labor_cost) AS total_labor_cost,
  SAFE_DIVIDE(SUM(hourly_labor_cost) + SUM(fulltime_labor_cost), SUM(net_sales)) AS labor_pct,
  SAFE_DIVIDE(SUM(hourly_labor_cost), SUM(net_sales)) AS hourly_pct,
  SAFE_DIVIDE(SUM(fulltime_labor_cost), SUM(net_sales)) AS fulltime_pct
FROM daily
GROUP BY week_start, week_end, iso_week;
