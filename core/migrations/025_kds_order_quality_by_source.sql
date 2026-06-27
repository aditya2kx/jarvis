-- 025_kds_order_quality_by_source.sql
-- Per-source daily p95 of per-ITEM KDS prep time. Sources (Per Diem, Uber Eats,
-- Point of Sale, …) come from square_kds_tickets.order_source. This view backs
-- panel 51 of the BHAGA Analytics dashboard (the KDS p95 per-source chart).
--
-- Per-item time = completion_time_sec / num_items, consistent with vw_kds_item_investigation
-- (migration 005). APPROX_QUANTILES gives the 95th percentile per (date, source).
-- No view-on-view: reads square_kds_tickets directly (matches migrations 009, 010).
-- All DDL is idempotent (CREATE OR REPLACE VIEW).
--
-- Apply: python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

-- ─────────────────────────────────────────────────────────────────────────────
-- vw_kds_order_quality_by_source_daily
-- Daily p95 of per-ITEM KDS prep time, one row per (date_local, order_source).
-- kds_p95_min = p95 of (completion_time_sec / num_items / 60.0).
-- Source: square_kds_tickets (no view-on-view).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_kds_order_quality_by_source_daily` AS
SELECT
  date_local                                                                AS date,
  order_source,
  COUNT(*)                                                                  AS kds_completed_tickets,
  APPROX_QUANTILES(completion_time_sec / num_items / 60.0, 100)[OFFSET(95)] AS kds_p95_min
FROM `jarvis-bhaga-prod.bhaga.square_kds_tickets`
WHERE completion_time_sec > 0
  AND num_items > 0
GROUP BY date_local, order_source;
