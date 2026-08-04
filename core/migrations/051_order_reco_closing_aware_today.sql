-- 051_order_reco_closing_aware_today.sql
-- Issue #215: On restock morning, keep today's delivery in
-- vw_order_reco_next_dates so Order Assistant shows today's After Restock
-- and chains Aug-next correctly. Drop today once ClickUp closing for today
-- exists (Current Qty has absorbed received tubs) — avoids the migration
-- 041 double-count that motivated strict `> today`.
--
-- Predicate (America/Chicago):
--   delivery_date > today
--   OR (delivery_date = today AND no base closing row for today)
--
-- TVFs / vw_order_reco_combined are unchanged — they read next_dates live.
-- After applying, call refresh_order_reco() so inventory_order_reco rows
-- bind to the new slot dates.

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_order_reco_next_dates` AS
SELECT delivery_date, slot
FROM (
  SELECT
    delivery_date,
    ROW_NUMBER() OVER (ORDER BY delivery_date) AS slot
  FROM (
    SELECT DISTINCT delivery_date
    FROM `jarvis-bhaga-prod.bhaga.inventory_restock_schedule`
    WHERE store = 'palmetto'
      AND (
        delivery_date > CURRENT_DATE('America/Chicago')
        OR (
          delivery_date = CURRENT_DATE('America/Chicago')
          AND NOT EXISTS (
            SELECT 1
            FROM `jarvis-bhaga-prod.bhaga.inventory_closing_daily` c
            WHERE c.store = 'palmetto'
              AND c.category = 'base'
              AND c.submitted_date = CURRENT_DATE('America/Chicago')
          )
        )
      )
  )
)
WHERE slot <= 2;
