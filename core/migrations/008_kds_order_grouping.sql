-- 008_kds_order_grouping.sql
-- Slow-items investigation: expose the order/ticket identity so the dashboard
-- table can group items by order and answer "did the whole order run slow, or
-- only certain items?". Adds ticket_name (Square ticket / order id) and
-- order_source (in-store / DoorDash / …) to vw_kds_item_investigation.
-- All DDL is idempotent (CREATE OR REPLACE VIEW).
--
-- Apply: python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

-- ─────────────────────────────────────────────────────────────────────────────
-- vw_kds_item_investigation (rev: + ticket_name, order_source)
-- Explodes items_in_ticket ("; "-delimited "<qty>x <name>") into per-item rows,
-- carrying the ticket identity so all items of one order share a ticket_name.
-- per_item_min = ROUND(completion_time_sec / num_items / 60) — integer minutes.
-- ticket_min applies to the whole order; per_item_min is the order's per-item
-- average, so within a ticket per_item_min is constant — the value is in
-- comparing tickets and seeing which orders ran long and what they contained.
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
    t.ticket_name,
    t.order_source,
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
  e.ticket_name,
  e.order_source,
  e.device_name,
  e.item_name,
  d.category,
  e.qty,
  e.per_item_min,
  e.ticket_min,
  e.num_items
FROM exploded e
LEFT JOIN dim d USING (item_name);
