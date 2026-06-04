-- 005_raw_parity.sql
-- Raw-scrape BQ parity: every Square/ADP/Reviews scrape gets a 1:1 raw BQ
-- table. Views for the 5-section Grafana dashboard are built on top.
-- All DDL is idempotent (CREATE TABLE IF NOT EXISTS, CREATE OR REPLACE VIEW).
--
-- Apply: python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

-- ─────────────────────────────────────────────────────────────────────────────
-- square_item_lines  (merge keys: transaction_id, line_seq)
-- Mirror of "BHAGA Square Raw" > item_lines tab. Adds employee (cashier)
-- that parse_item_sales_csv reads but was previously dropped.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.square_item_lines` (
  date_local            DATE      NOT NULL,
  item_sold_at_local    STRING,
  item_name             STRING,
  category              STRING,
  qty_sold              INT64,
  gross_sales_cents     INT64,
  discount_cents        INT64,
  net_sales_cents       INT64,
  event_type            STRING,
  transaction_id        STRING,
  payment_id            STRING,
  location              STRING,
  channel               STRING,
  employee              STRING,
  line_seq              INT64,
  scraped_at_utc        TIMESTAMP
) PARTITION BY date_local;

-- ─────────────────────────────────────────────────────────────────────────────
-- square_kds_daily  (merge key: date_local)
-- Mirror of "BHAGA Square Raw" > kds_daily tab (daily KDS aggregates).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.square_kds_daily` (
  date_local                  DATE      NOT NULL,
  completed_tickets           INT64,
  completed_items             INT64,
  median_time_per_item_sec    FLOAT64,
  p90_time_per_item_sec       FLOAT64,
  p95_time_per_item_sec       FLOAT64,
  p99_time_per_item_sec       FLOAT64,
  pct_tickets_late            FLOAT64,
  shift_start                 STRING,
  shift_end                   STRING,
  late_tickets                INT64,
  due_tickets                 INT64,
  per_item_times_json         STRING,
  scraped_at_utc              TIMESTAMP
);

-- ─────────────────────────────────────────────────────────────────────────────
-- square_kds_tickets  (merge keys: date_local, time_created, ticket_name)
-- NEW grain — per-ticket KDS rows; required for "items over X min" drilldown.
-- Per-item minutes = completion_time_sec / num_items.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.square_kds_tickets` (
  date_local          DATE      NOT NULL,
  device_name         STRING,
  ticket_name         STRING,
  order_source        STRING,
  num_items           INT64,
  items_in_ticket     STRING,
  completion_time_sec FLOAT64,
  time_created        STRING,
  time_completed      STRING,
  time_due            STRING,
  scraped_at_utc      TIMESTAMP
) PARTITION BY date_local;

-- ─────────────────────────────────────────────────────────────────────────────
-- adp_earnings  (merge keys: period_start, period_end, employee, description, check_date)
-- NEW — per earning-line from ADP Earnings & Hours XLSX. amount can be negative
-- (void/reissue). Codes seen: Regular, Overtime, Bonus, Credit Card Tips Owed,
-- Misc reimbursement non-taxable.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.adp_earnings` (
  period_start        DATE      NOT NULL,
  period_end          DATE,
  check_date          DATE,
  employee            STRING    NOT NULL,
  raw_employee_name   STRING,
  description         STRING,
  hours               FLOAT64,
  hourly_rate         FLOAT64,
  amount              FLOAT64,
  scraped_at_utc      TIMESTAMP
);

-- ─────────────────────────────────────────────────────────────────────────────
-- google_reviews  (merge key: review_id)
-- Mirror of "BHAGA Review Raw" > reviews tab.
-- review_id is a sha1 hash of (post_ts + reviewer + comment prefix), stable.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.google_reviews` (
  review_id               STRING    NOT NULL,
  post_ts_ct              STRING,
  post_date_ct            DATE,
  rating                  INT64,
  reviewer                STRING,
  comment                 STRING,
  named_baristas          STRING,
  named_status            STRING,
  shift_date_credited     STRING,
  shift_assignment_reason STRING,
  shift_members           STRING,
  trainees_on_shift       STRING,
  named_credit_each       FLOAT64,
  base_credit_each        FLOAT64,
  total_bonus             FLOAT64,
  review_url              STRING,
  ingested_at_utc         TIMESTAMP
);

-- ─────────────────────────────────────────────────────────────────────────────
-- vw_order_quality_daily
-- KDS per-item times converted to minutes for Grafana (decimals handled in panel).
-- Source: model_labor_daily (no view-on-view — model_labor_daily is a raw table).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_order_quality_daily` AS
SELECT
  date,
  kds_completed_items,
  kds_completed_tickets,
  kds_median_time_per_item_sec / 60.0  AS kds_median_min,
  kds_p90_time_per_item_sec / 60.0     AS kds_p90_min,
  kds_p95_time_per_item_sec / 60.0     AS kds_p95_min,
  kds_p99_time_per_item_sec / 60.0     AS kds_p99_min,
  kds_pct_items_over_goal,
  kds_pct_tickets_late
FROM `jarvis-bhaga-prod.bhaga.model_labor_daily`;

-- ─────────────────────────────────────────────────────────────────────────────
-- vw_kds_item_investigation
-- Explodes items_in_ticket ("; "-delimited "<qty>x <name>") into per-item rows.
-- per_item_min = ROUND(completion_time_sec / num_items / 60) — integer minutes.
-- category is best-effort: matched from square_item_lines dimension.
-- Source: square_kds_tickets + square_item_lines (no view-on-view).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_kds_item_investigation` AS
WITH dim AS (
  SELECT item_name, ANY_VALUE(category) AS category
  FROM `jarvis-bhaga-prod.bhaga.square_item_lines`
  GROUP BY item_name
),
exploded AS (
  SELECT
    t.date_local,
    t.time_created,
    t.device_name,
    t.num_items,
    CAST(ROUND(SAFE_DIVIDE(t.completion_time_sec, t.num_items) / 60.0) AS INT64) AS per_item_min,
    CAST(ROUND(t.completion_time_sec / 60.0) AS INT64)                            AS ticket_min,
    CAST(REGEXP_EXTRACT(seg, r'^(\d+)x ') AS INT64)                               AS qty,
    TRIM(REGEXP_REPLACE(seg, r'^\d+x ', ''))                                      AS item_name
  FROM `jarvis-bhaga-prod.bhaga.square_kds_tickets` t,
       UNNEST(SPLIT(t.items_in_ticket, '; ')) AS seg
  WHERE t.num_items > 0 AND t.completion_time_sec > 0
)
SELECT
  e.date_local,
  e.time_created,
  e.device_name,
  e.item_name,
  d.category,
  e.qty,
  e.per_item_min,
  e.ticket_min,
  e.num_items
FROM exploded e
LEFT JOIN dim d USING (item_name);

-- ─────────────────────────────────────────────────────────────────────────────
-- vw_staff_on_shift
-- Who worked a given date (date-level only — no per-item employee attribution).
-- Source: adp_shifts (no view-on-view).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_staff_on_shift` AS
SELECT
  date,
  canonical_name AS employee,
  in_time,
  out_time,
  total_hours
FROM `jarvis-bhaga-prod.bhaga.adp_shifts`;

-- ─────────────────────────────────────────────────────────────────────────────
-- vw_model_labor_daily  (replaces 004 definition — adds labor hours metrics)
-- Source: model_labor_daily (no view-on-view).
-- New: total_hours, hourly_hours_per_item, fulltime_hours_per_item,
--      *_hours_per_1k_net_sales for Labor section charts.
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
  (hourly_hours + fulltime_hours)                             AS total_hours,
  hourly_hours_per_item,
  fulltime_hours_per_item,
  SAFE_DIVIDE(hourly_hours + fulltime_hours, net_sales) * 1000 AS total_hours_per_1k_net_sales,
  SAFE_DIVIDE(hourly_hours, net_sales) * 1000                 AS hourly_hours_per_1k_net_sales,
  SAFE_DIVIDE(fulltime_hours, net_sales) * 1000               AS fulltime_hours_per_1k_net_sales,
  hourly_pct_of_net_sales                                     AS hourly_pct,
  fulltime_pct_of_net_sales                                   AS fulltime_pct,
  total_labor_pct_of_net_sales                                AS labor_pct,
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
-- vw_model_labor_weekly  (replaces 004 definition — adds labor hours metrics)
-- Source: model_labor_weekly (no view-on-view).
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
  (hourly_hours + fulltime_hours)                             AS total_hours,
  hourly_hours_per_item,
  fulltime_hours_per_item,
  SAFE_DIVIDE(hourly_hours + fulltime_hours, net_sales) * 1000 AS total_hours_per_1k_net_sales,
  SAFE_DIVIDE(hourly_hours, net_sales) * 1000                 AS hourly_hours_per_1k_net_sales,
  SAFE_DIVIDE(fulltime_hours, net_sales) * 1000               AS fulltime_hours_per_1k_net_sales,
  total_labor_pct_of_net_sales                                AS labor_pct,
  hourly_pct_of_net_sales                                     AS hourly_pct,
  fulltime_pct_of_net_sales                                   AS fulltime_pct,
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
-- vw_model_payroll_period  (replaces 004 definition — adds ADP actuals + diffs)
-- Sources: model_tip_alloc_period, model_review_bonus_period, adp_wage_rates,
--          adp_earnings — no view-on-view.
-- est_gross_pay = hours_worked × wage_rate_dollars (base rate only; no OT split).
-- ADP actual wages: SUM of Regular+Overtime earnings lines from adp_earnings.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_model_payroll_period` AS
WITH earn AS (
  SELECT
    period_start,
    period_end,
    employee,
    SUM(IF(description IN (
        'Regular', 'Overtime', 'Double Overtime', 'Holiday', 'Salary'
      ), amount, 0))                                                              AS adp_wages_paid,
    SUM(IF(description = 'Bonus', amount, 0))                                    AS adp_bonus_paid,
    SUM(IF(description = 'Credit Card Tips Owed', amount, 0))                    AS adp_tips_paid,
    SUM(IF(
        description NOT LIKE '%reimbursement%'
        AND description NOT LIKE '%Cash tips%',
      amount, 0))                                                                  AS adp_total_paid
  FROM `jarvis-bhaga-prod.bhaga.adp_earnings`
  GROUP BY period_start, period_end, employee
)
SELECT
  t.period_start,
  t.period_end,
  t.is_open,
  t.employee,
  t.hours_worked,
  ROUND(t.hours_worked * w.wage_rate_dollars, 2)                                  AS est_gross_pay,
  t.our_calc                                                                       AS tips_allocated,
  COALESCE(r.total_bonus, 0)                                                       AS review_bonus,
  ROUND(
    COALESCE(t.hours_worked * w.wage_rate_dollars, 0)
    + COALESCE(t.our_calc, 0)
    + COALESCE(r.total_bonus, 0),
  2)                                                                                AS est_total_pay,
  e.adp_wages_paid,
  e.adp_tips_paid,
  e.adp_bonus_paid,
  e.adp_total_paid,
  ROUND(t.hours_worked * w.wage_rate_dollars - COALESCE(e.adp_wages_paid, 0), 2) AS wage_diff,
  ROUND(t.our_calc - COALESCE(e.adp_tips_paid, t.adp_paid), 2)                   AS tip_diff,
  ROUND(COALESCE(r.total_bonus, 0) - COALESCE(e.adp_bonus_paid, 0), 2)           AS bonus_diff
FROM `jarvis-bhaga-prod.bhaga.model_tip_alloc_period` t
LEFT JOIN `jarvis-bhaga-prod.bhaga.model_review_bonus_period` r
  USING (period_start, period_end, employee)
LEFT JOIN `jarvis-bhaga-prod.bhaga.adp_wage_rates` w
  ON t.employee = w.canonical_name
LEFT JOIN earn e
  ON t.period_start = e.period_start
  AND t.period_end  = e.period_end
  AND t.employee    = e.employee;
