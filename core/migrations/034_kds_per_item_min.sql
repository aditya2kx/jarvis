-- 034_kds_per_item_min.sql
-- Per-order KDS per-item prep time at full FLOAT64 precision, one row per
-- ticket. Neither existing KDS view fits the operator console's grain-aware
-- percentile rollup: vw_order_quality_daily derives its percentiles from
-- pre-collapsed per-day columns in model_labor_daily (can't be re-aggregated
-- into weekly/monthly buckets, and ignores order_source entirely);
-- vw_kds_order_quality_daily/vw_kds_order_quality_by_source_daily are
-- already collapsed to one row per day(+source) (same re-aggregation
-- problem). This view exposes the raw per-ticket ratio so
-- APPROX_QUANTILES(...) can be computed at any GROUP BY <bucket>, optionally
-- filtered by order_source, per apps/operator-console/lib/bq/queries.ts
-- `orderQualityByGrain`.
-- All DDL is idempotent (CREATE OR REPLACE VIEW).
--
-- Apply: python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

-- ─────────────────────────────────────────────────────────────────────────────
-- vw_kds_per_item_min
-- Source: square_kds_tickets (no view-on-view).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_kds_per_item_min` AS
SELECT
  date_local                                AS date,
  order_source,
  SAFE_DIVIDE(completion_time_sec, num_items) / 60.0   AS per_item_min
FROM `jarvis-bhaga-prod.bhaga.square_kds_tickets`
WHERE completion_time_sec > 0 AND num_items > 0;
