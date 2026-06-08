-- 010_kds_order_quality.sql
-- Order-level KDS time distribution. The existing vw_order_quality_daily derives
-- its percentiles from per-ITEM seconds (order time ÷ items) in model_labor_daily.
-- Since KDS records completion time per ORDER (ticket), the dashboard's
-- distribution chart should be percentiles of whole-order prep time. This view
-- computes them straight from the raw tickets, per day.
-- All DDL is idempotent (CREATE OR REPLACE VIEW).
--
-- Apply: python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

-- ─────────────────────────────────────────────────────────────────────────────
-- vw_kds_order_quality_daily
-- Daily median / p90 / p95 / p99 of ORDER completion time (minutes), one row per
-- day. APPROX_QUANTILES(...,100)[OFFSET(n)] gives the nth percentile. No outlier
-- filtering here (raw truth); the dashboard caps the y-axis to handle skew.
-- Source: square_kds_tickets (no view-on-view).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_kds_order_quality_daily` AS
SELECT
  date_local                                                       AS date,
  COUNT(*)                                                         AS kds_completed_tickets,
  APPROX_QUANTILES(completion_time_sec / 60.0, 100)[OFFSET(50)]    AS order_median_min,
  APPROX_QUANTILES(completion_time_sec / 60.0, 100)[OFFSET(90)]    AS order_p90_min,
  APPROX_QUANTILES(completion_time_sec / 60.0, 100)[OFFSET(95)]    AS order_p95_min,
  APPROX_QUANTILES(completion_time_sec / 60.0, 100)[OFFSET(99)]    AS order_p99_min
FROM `jarvis-bhaga-prod.bhaga.square_kds_tickets`
WHERE completion_time_sec > 0
GROUP BY date_local;
