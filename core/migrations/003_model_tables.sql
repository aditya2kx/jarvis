-- 003_model_tables.sql
-- Materialized model tables for BHAGA computed analytics.
-- These mirror the bhaga_model Google Sheet tabs — populated by
-- agents/bhaga/scripts/materialize_model_bq.py (called by daily_refresh.py).
-- Grafana panels that need computed model columns bind to vw_model_* views
-- defined at the end of this file.
--
-- Merge key columns are marked in comments; materialize_model_bq uses them
-- for MERGE/INSERT-UPDATE idempotent writes via core.datastore.load_rows.

-- ─────────────────────────────────────────────────────────────────────────────
-- model_daily  (merge key: date)
-- One row per calendar day — from the "daily" Sheet tab.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.model_daily` (
  date                  DATE    NOT NULL,    -- merge key
  dow                   STRING,
  gross_sales           FLOAT64,
  tip_pool              FLOAT64,
  tips_pct_of_sales     FLOAT64,
  team_hours_eligible   FLOAT64,
  team_hours_total      FLOAT64,
  pool_per_hour         FLOAT64,
  txn_count             INT64,
  materialized_at_utc   TIMESTAMP
);

-- ─────────────────────────────────────────────────────────────────────────────
-- model_labor_daily  (merge key: date)
-- One row per calendar day — from the "labor_daily" tab.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.model_labor_daily` (
  date                              DATE    NOT NULL,    -- merge key
  dow                               STRING,
  gross_sales                       FLOAT64,
  discounts                         FLOAT64,
  net_sales                         FLOAT64,
  tip_pool                          FLOAT64,
  net_sales_plus_tips               FLOAT64,
  orders                            INT64,
  hourly_hours                      FLOAT64,
  hourly_labor_cost                 FLOAT64,
  fulltime_hours                    FLOAT64,
  fulltime_labor_cost               FLOAT64,
  total_labor_cost                  FLOAT64,
  hourly_pct_of_net_sales           FLOAT64,
  hourly_pct_of_net_sales_plus_tips FLOAT64,
  fulltime_pct_of_net_sales         FLOAT64,
  fulltime_pct_of_net_sales_plus_tips FLOAT64,
  total_labor_pct_of_net_sales      FLOAT64,
  total_labor_pct_of_net_sales_plus_tips FLOAT64,
  tips_pct_of_net_sales             FLOAT64,
  all_in_cost_pct_of_net_sales_plus_tips FLOAT64,
  hourly_labor_per_order            FLOAT64,
  fulltime_labor_per_order          FLOAT64,
  total_labor_per_order             FLOAT64,
  orders_per_labor_hour             FLOAT64,
  peak_hour_orders_per_labor_hour   FLOAT64,
  over_saturation                   BOOL,
  hours_per_order                   FLOAT64,
  avg_order_price                   FLOAT64,
  avg_net_sales_plus_tips_per_order FLOAT64,
  items_sold                        INT64,
  avg_items_per_order               FLOAT64,
  hours_per_item                    FLOAT64,
  avg_item_price                    FLOAT64,
  hourly_hours_per_order            FLOAT64,
  fulltime_hours_per_order          FLOAT64,
  hourly_hours_per_item             FLOAT64,
  fulltime_hours_per_item           FLOAT64,
  kds_completed_tickets             INT64,
  kds_completed_items               INT64,
  kds_median_time_per_item_sec      FLOAT64,
  kds_p90_time_per_item_sec         FLOAT64,
  kds_p95_time_per_item_sec         FLOAT64,
  kds_p99_time_per_item_sec         FLOAT64,
  kds_pct_items_over_goal           FLOAT64,
  kds_pct_tickets_late              FLOAT64,
  outlier_flag                      BOOL,
  forecast_exclude                  BOOL,
  outlier_reason                    STRING,
  forecast_exclude_reason           STRING,
  materialized_at_utc               TIMESTAMP
);

-- ─────────────────────────────────────────────────────────────────────────────
-- model_labor_weekly  (merge key: iso_week)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.model_labor_weekly` (
  iso_week                          STRING  NOT NULL,  -- merge key e.g. "2026-W20"
  week_start                        DATE,
  week_end                          DATE,
  is_partial                        BOOL,
  days_covered                      INT64,
  gross_sales                       FLOAT64,
  discounts                         FLOAT64,
  net_sales                         FLOAT64,
  tip_pool                          FLOAT64,
  net_sales_plus_tips               FLOAT64,
  orders                            INT64,
  hourly_hours                      FLOAT64,
  hourly_labor_cost                 FLOAT64,
  fulltime_hours                    FLOAT64,
  fulltime_labor_cost               FLOAT64,
  total_labor_cost                  FLOAT64,
  hourly_pct_of_net_sales           FLOAT64,
  hourly_pct_of_net_sales_plus_tips FLOAT64,
  fulltime_pct_of_net_sales         FLOAT64,
  fulltime_pct_of_net_sales_plus_tips FLOAT64,
  total_labor_pct_of_net_sales      FLOAT64,
  total_labor_pct_of_net_sales_plus_tips FLOAT64,
  tips_pct_of_net_sales             FLOAT64,
  all_in_cost_pct_of_net_sales_plus_tips FLOAT64,
  hourly_labor_per_order            FLOAT64,
  fulltime_labor_per_order          FLOAT64,
  total_labor_per_order             FLOAT64,
  orders_per_labor_hour             FLOAT64,
  peak_hour_orders_per_labor_hour   FLOAT64,
  over_saturation                   BOOL,
  hours_per_order                   FLOAT64,
  avg_order_price                   FLOAT64,
  avg_net_sales_plus_tips_per_order FLOAT64,
  items_sold                        INT64,
  avg_items_per_order               FLOAT64,
  hours_per_item                    FLOAT64,
  avg_item_price                    FLOAT64,
  hourly_hours_per_order            FLOAT64,
  fulltime_hours_per_order          FLOAT64,
  hourly_hours_per_item             FLOAT64,
  fulltime_hours_per_item           FLOAT64,
  kds_completed_tickets             INT64,
  kds_completed_items               INT64,
  kds_median_time_per_item_sec      FLOAT64,
  kds_p90_time_per_item_sec         FLOAT64,
  kds_p95_time_per_item_sec         FLOAT64,
  kds_p99_time_per_item_sec         FLOAT64,
  kds_pct_items_over_goal           FLOAT64,
  kds_pct_tickets_late              FLOAT64,
  materialized_at_utc               TIMESTAMP
);

-- ─────────────────────────────────────────────────────────────────────────────
-- model_labor_period  (merge key: pay_period_start)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.model_labor_period` (
  pay_period_start                  DATE    NOT NULL,  -- merge key
  pay_period_end                    DATE,
  is_open                           BOOL,
  days_covered                      INT64,
  gross_sales                       FLOAT64,
  discounts                         FLOAT64,
  net_sales                         FLOAT64,
  tip_pool                          FLOAT64,
  net_sales_plus_tips               FLOAT64,
  orders                            INT64,
  hourly_hours                      FLOAT64,
  hourly_labor_cost                 FLOAT64,
  fulltime_hours                    FLOAT64,
  fulltime_labor_cost               FLOAT64,
  total_labor_cost                  FLOAT64,
  hourly_pct_of_net_sales           FLOAT64,
  hourly_pct_of_net_sales_plus_tips FLOAT64,
  fulltime_pct_of_net_sales         FLOAT64,
  fulltime_pct_of_net_sales_plus_tips FLOAT64,
  total_labor_pct_of_net_sales      FLOAT64,
  total_labor_pct_of_net_sales_plus_tips FLOAT64,
  tips_pct_of_net_sales             FLOAT64,
  all_in_cost_pct_of_net_sales_plus_tips FLOAT64,
  hourly_labor_per_order            FLOAT64,
  fulltime_labor_per_order          FLOAT64,
  total_labor_per_order             FLOAT64,
  orders_per_labor_hour             FLOAT64,
  peak_hour_orders_per_labor_hour   FLOAT64,
  over_saturation                   BOOL,
  hours_per_order                   FLOAT64,
  avg_order_price                   FLOAT64,
  avg_net_sales_plus_tips_per_order FLOAT64,
  items_sold                        INT64,
  avg_items_per_order               FLOAT64,
  hours_per_item                    FLOAT64,
  avg_item_price                    FLOAT64,
  hourly_hours_per_order            FLOAT64,
  fulltime_hours_per_order          FLOAT64,
  hourly_hours_per_item             FLOAT64,
  fulltime_hours_per_item           FLOAT64,
  kds_completed_tickets             INT64,
  kds_completed_items               INT64,
  kds_median_time_per_item_sec      FLOAT64,
  kds_p90_time_per_item_sec         FLOAT64,
  kds_p95_time_per_item_sec         FLOAT64,
  kds_p99_time_per_item_sec         FLOAT64,
  kds_pct_items_over_goal           FLOAT64,
  kds_pct_tickets_late              FLOAT64,
  materialized_at_utc               TIMESTAMP
);

-- ─────────────────────────────────────────────────────────────────────────────
-- model_tip_alloc_period  (merge key: period_start + employee)
-- One row per employee per pay period — from "tip_alloc_period" tab.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.model_tip_alloc_period` (
  period_start        DATE    NOT NULL,  -- merge key part 1
  period_end          DATE,
  coverage            STRING,
  is_open             BOOL,
  employee            STRING  NOT NULL,  -- merge key part 2
  hours_worked        FLOAT64,
  our_calc            FLOAT64,
  adp_paid            FLOAT64,
  diff                FLOAT64,
  diff_pct            FLOAT64,
  our_per_hour        FLOAT64,
  adp_per_hour        FLOAT64,
  likely_reason       STRING,
  materialized_at_utc TIMESTAMP
);

-- ─────────────────────────────────────────────────────────────────────────────
-- model_tip_alloc_daily  (merge key: date + employee)
-- One row per employee per day — from "tip_alloc_daily" tab.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.model_tip_alloc_daily` (
  date                DATE    NOT NULL,  -- merge key part 1
  dow                 STRING,
  period_start        DATE,
  period_end          DATE,
  employee            STRING  NOT NULL,  -- merge key part 2
  hours_worked        FLOAT64,
  day_pool            FLOAT64,
  team_hours_eligible FLOAT64,
  pct_of_day_hours    FLOAT64,
  our_share           FLOAT64,
  materialized_at_utc TIMESTAMP
);

-- ─────────────────────────────────────────────────────────────────────────────
-- model_period_summary  (merge key: period_start)
-- One row per pay period — from "period_summary" tab.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.model_period_summary` (
  period_start                    DATE    NOT NULL,  -- merge key
  period_end                      DATE,
  coverage                        STRING,
  is_open                         BOOL,
  check_dates                     STRING,
  employees_count                 INT64,
  team_hours                      FLOAT64,
  tip_pool                        FLOAT64,
  our_total_allocated             FLOAT64,
  adp_total_paid                  FLOAT64,
  total_diff                      FLOAT64,
  employees_with_diff_over_1usd   INT64,
  materialized_at_utc             TIMESTAMP
);

-- ─────────────────────────────────────────────────────────────────────────────
-- vw_model_labor_daily  — Grafana-ready model rows with label columns
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_model_labor_daily` AS
SELECT
  date,
  dow,
  gross_sales,
  net_sales,
  tip_pool,
  net_sales_plus_tips,
  orders,
  hourly_hours,
  fulltime_hours,
  hourly_hours + fulltime_hours                         AS total_hours,
  hourly_labor_cost + fulltime_labor_cost               AS total_labor_cost,
  total_labor_pct_of_net_sales                          AS labor_pct,
  tips_pct_of_net_sales                                 AS tips_pct,
  orders_per_labor_hour,
  over_saturation,
  outlier_flag,
  forecast_exclude
FROM `jarvis-bhaga-prod.bhaga.model_labor_daily`;

-- ─────────────────────────────────────────────────────────────────────────────
-- vw_model_period_summary  — period-level tip allocation overview
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_model_period_summary` AS
SELECT
  period_start,
  period_end,
  coverage,
  is_open,
  employees_count,
  team_hours,
  tip_pool,
  our_total_allocated,
  adp_total_paid,
  total_diff,
  SAFE_DIVIDE(total_diff, our_total_allocated) AS diff_pct,
  employees_with_diff_over_1usd
FROM `jarvis-bhaga-prod.bhaga.model_period_summary`;
