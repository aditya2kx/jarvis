-- 002_views.sql
-- Curated BI views over raw BHAGA tables.
-- These are the stable Grafana contract — panels bind to vw_* not the raw tables
-- so internal schema changes don't break dashboards.
--
-- All monetary values stay in CENTS throughout (divide by 100 for display in Grafana).
-- The BI tool calculated fields handle formatting.

-- ─────────────────────────────────────────────────────────────────────────────
-- vw_daily_sales
-- One row per calendar day: gross sales, net sales, tips, transaction count.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_daily_sales` AS
SELECT
  date_local,
  COUNT(*)                          AS txn_count,
  SUM(gross_sales_cents)            AS gross_sales_cents,
  SUM(net_sales_cents)              AS net_sales_cents,
  SUM(tip_cents)                    AS tip_cents,
  SUM(total_collected_cents)        AS total_collected_cents,
  SUM(discount_cents)               AS discount_cents,
  SAFE_DIVIDE(SUM(tip_cents), SUM(gross_sales_cents)) AS tip_rate
FROM `jarvis-bhaga-prod.bhaga.square_transactions`
WHERE event_type NOT IN ('REFUND', 'PARTIAL_REFUND')
   OR event_type IS NULL
GROUP BY date_local;

-- ─────────────────────────────────────────────────────────────────────────────
-- vw_tips_by_hour
-- Tips and sales bucketed by hour-of-day — useful for operational scheduling.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_tips_by_hour` AS
SELECT
  date_local,
  EXTRACT(HOUR FROM TIMESTAMP(created_at_local_iso)) AS hour_local,
  COUNT(*)                    AS txn_count,
  SUM(tip_cents)              AS tip_cents,
  SUM(gross_sales_cents)      AS gross_sales_cents
FROM `jarvis-bhaga-prod.bhaga.square_transactions`
WHERE created_at_local_iso IS NOT NULL
  AND created_at_local_iso != ''
  AND (event_type NOT IN ('REFUND', 'PARTIAL_REFUND') OR event_type IS NULL)
GROUP BY date_local, hour_local;

-- ─────────────────────────────────────────────────────────────────────────────
-- vw_labor_daily
-- One row per employee per day: total hours worked.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_labor_daily` AS
SELECT
  date,
  canonical_name                           AS employee_name,
  employee_id,
  regular_hours,
  ot_hours,
  doubletime_hours,
  total_hours,
  scraped_at_utc
FROM `jarvis-bhaga-prod.bhaga.adp_shifts`;

-- ─────────────────────────────────────────────────────────────────────────────
-- vw_labor_weekly
-- Hours rolled up by ISO week per employee.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_labor_weekly` AS
SELECT
  DATE_TRUNC(date, WEEK(MONDAY))  AS week_start,
  canonical_name                   AS employee_name,
  employee_id,
  SUM(regular_hours)               AS regular_hours,
  SUM(ot_hours)                    AS ot_hours,
  SUM(doubletime_hours)            AS doubletime_hours,
  SUM(total_hours)                 AS total_hours
FROM `jarvis-bhaga-prod.bhaga.adp_shifts`
GROUP BY week_start, employee_name, employee_id;

-- ─────────────────────────────────────────────────────────────────────────────
-- vw_sales_labor_daily
-- Joins daily sales with daily labor hours — used for labor% and saturation.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_sales_labor_daily` AS
SELECT
  s.date_local,
  s.gross_sales_cents,
  s.net_sales_cents,
  s.tip_cents,
  s.txn_count,
  COALESCE(l.total_hours, 0)   AS total_labor_hours,
  COALESCE(l.regular_hours, 0) AS regular_hours,
  -- labor cost proxy: wage_rate × hours (requires wage_rates join for full calc)
  SAFE_DIVIDE(s.net_sales_cents, NULLIF(COALESCE(l.total_hours, 0), 0))
                               AS net_sales_cents_per_labor_hour
FROM (
  SELECT
    date_local,
    SUM(gross_sales_cents)  AS gross_sales_cents,
    SUM(net_sales_cents)    AS net_sales_cents,
    SUM(tip_cents)          AS tip_cents,
    COUNT(*)                AS txn_count
  FROM `jarvis-bhaga-prod.bhaga.square_transactions`
  WHERE event_type NOT IN ('REFUND', 'PARTIAL_REFUND') OR event_type IS NULL
  GROUP BY date_local
) s
LEFT JOIN (
  SELECT date, SUM(total_hours) AS total_hours, SUM(regular_hours) AS regular_hours
  FROM `jarvis-bhaga-prod.bhaga.adp_shifts`
  GROUP BY date
) l ON l.date = s.date_local;

-- ─────────────────────────────────────────────────────────────────────────────
-- vw_employee_hours_summary
-- Per-employee total and average hours over all recorded dates (for filters).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_employee_hours_summary` AS
SELECT
  canonical_name           AS employee_name,
  employee_id,
  COUNT(DISTINCT date)     AS days_worked,
  SUM(total_hours)         AS total_hours,
  AVG(total_hours)         AS avg_hours_per_day,
  MIN(date)                AS first_shift_date,
  MAX(date)                AS last_shift_date
FROM `jarvis-bhaga-prod.bhaga.adp_shifts`
GROUP BY employee_name, employee_id;
