-- 009_kds_order_level.sql
-- Order-level KDS investigation: one row per ticket (order), so the dashboard
-- can report slow ORDERS (not exploded items) and flag any order whose total
-- prep time exceeds num_items × slow-item threshold. Carries order start/end
-- times and the full item list (with quantities) for the order.
-- All DDL is idempotent (CREATE OR REPLACE VIEW).
--
-- Apply: python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

-- ─────────────────────────────────────────────────────────────────────────────
-- vw_kds_order_investigation
-- One row per Square KDS ticket. order_min = whole-order prep time (min).
-- start_time / end_time are the local HH:MM:SS extracted from the source
-- timestamps. items_in_ticket is the "; "-delimited "<qty>x <name>" list, so a
-- single row shows every item and quantity in the order. The "is this order
-- slow?" decision (order_min > num_items × threshold) is applied by the panel
-- using the dashboard's $max_item_min variable, not baked into the view.
-- Source: square_kds_tickets (no view-on-view).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_kds_order_investigation` AS
SELECT
  date_local,
  ticket_name,
  order_source,
  device_name,
  time_created,
  SUBSTR(time_created, 12, 8)                                AS start_time,
  SUBSTR(time_completed, 12, 8)                              AS end_time,
  num_items,
  CAST(ROUND(completion_time_sec / 60.0) AS INT64)           AS order_min,
  items_in_ticket
FROM `jarvis-bhaga-prod.bhaga.square_kds_tickets`
WHERE num_items > 0 AND completion_time_sec > 0;
