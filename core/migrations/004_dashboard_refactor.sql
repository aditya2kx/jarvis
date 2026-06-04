-- 004_dashboard_refactor.sql
-- Dashboard refactor: adds model_review_bonus_period table (M3) and
-- creates/extends views for the 3-section Grafana dashboard (M5).
--
-- Apply: python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"
-- All DDL is idempotent (CREATE TABLE IF NOT EXISTS, CREATE OR REPLACE VIEW).

-- ─────────────────────────────────────────────────────────────────────────────
-- model_review_bonus_period  (merge keys: period_start, employee)
-- One row per employee per pay period — review bonus rollup built by
-- process_reviews.py / build_period_rollup. Mirrors the review_bonus_period
-- Google Sheet tab when BHAGA_SHEET_FROM_BQ=1.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.model_review_bonus_period` (
  period_start         DATE    NOT NULL,   -- merge key
  period_end           DATE    NOT NULL,
  is_open              BOOL,
  employee             STRING  NOT NULL,   -- merge key
  reviews_credited     INT64,
  named_count          INT64,
  base_dollars         FLOAT64,
  named_dollars        FLOAT64,
  total_bonus          FLOAT64,
  likely_reason        STRING,
  materialized_at_utc  TIMESTAMP
);

-- ─────────────────────────────────────────────────────────────────────────────
-- vw_model_labor_daily  (extended; no view-on-view — source: model_labor_daily)
-- Extends the view from 003 with items_sold, hours_per_item,
-- and dedicated hourly_pct / fulltime_pct aliases for the Labor Cost section.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_model_labor_daily` AS
SELECT
  date,
  dow,
  gross_sales,
  discounts,
  net_sales,
  tip_pool,
  net_sales_plus_tips,
  orders,
  hourly_hours,
  hourly_labor_cost,
  fulltime_hours,
  fulltime_labor_cost,
  total_labor_cost,
  hourly_pct_of_net_sales                AS hourly_pct,
  fulltime_pct_of_net_sales              AS fulltime_pct,
  total_labor_pct_of_net_sales           AS labor_pct,
  total_labor_pct_of_net_sales,
  hourly_pct_of_net_sales,
  fulltime_pct_of_net_sales,
  tips_pct_of_net_sales,
  items_sold,
  hours_per_item,
  orders_per_labor_hour,
  avg_order_price,
  avg_items_per_order,
  materialized_at_utc
FROM `jarvis-bhaga-prod.bhaga.model_labor_daily`;

-- ─────────────────────────────────────────────────────────────────────────────
-- vw_model_labor_weekly  (new; source: model_labor_weekly only, no view-on-view)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_model_labor_weekly` AS
SELECT
  iso_week,
  week_start,
  week_end,
  is_partial,
  days_covered,
  gross_sales,
  discounts,
  net_sales,
  tip_pool,
  net_sales_plus_tips,
  orders,
  hourly_hours,
  hourly_labor_cost,
  fulltime_hours,
  fulltime_labor_cost,
  total_labor_cost,
  total_labor_pct_of_net_sales           AS labor_pct,
  hourly_pct_of_net_sales                AS hourly_pct,
  fulltime_pct_of_net_sales              AS fulltime_pct,
  total_labor_pct_of_net_sales,
  hourly_pct_of_net_sales,
  fulltime_pct_of_net_sales,
  tips_pct_of_net_sales,
  items_sold,
  hours_per_item,
  orders_per_labor_hour,
  avg_order_price,
  avg_items_per_order,
  materialized_at_utc
FROM `jarvis-bhaga-prod.bhaga.model_labor_weekly`;

-- ─────────────────────────────────────────────────────────────────────────────
-- vw_model_payroll_period  (new; sources: model_tip_alloc_period, model_review_bonus_period,
--                           adp_wage_rates — no view-on-view)
-- Consolidates hours, estimated gross pay, tips allocated, and review bonus
-- into a single payroll-period view for the Payroll Grafana section.
-- est_gross_pay = hours_worked × wage_rate_dollars (base rate only; OT not split).
-- Employees with no wage_rate row (salaried / unmatched) get NULL est_gross_pay.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_model_payroll_period` AS
SELECT
  t.period_start,
  t.period_end,
  t.is_open,
  t.employee,
  t.hours_worked,
  ROUND(t.hours_worked * w.wage_rate_dollars, 2)                              AS est_gross_pay,
  t.our_calc                                                                   AS tips_allocated,
  t.adp_paid                                                                   AS adp_tips_paid,
  COALESCE(r.total_bonus, 0)                                                   AS review_bonus,
  ROUND(
    COALESCE(t.hours_worked * w.wage_rate_dollars, 0)
    + COALESCE(t.our_calc, 0)
    + COALESCE(r.total_bonus, 0),
  2)                                                                            AS est_total_pay
FROM `jarvis-bhaga-prod.bhaga.model_tip_alloc_period` t
LEFT JOIN `jarvis-bhaga-prod.bhaga.model_review_bonus_period` r
  USING (period_start, period_end, employee)
LEFT JOIN `jarvis-bhaga-prod.bhaga.adp_wage_rates` w
  ON t.employee = w.canonical_name;
