-- 032_order_reco_combined.sql
-- Issue #137 iteration: collapse the two per-date Order Recommendation
-- panels (031's vw_order_reco_slot1/slot2, Grafana panels 81/82) into one
-- combined table (panel 83), per operator feedback:
--   (1) methodology text was too long (needs no-scroll trim -- Grafana-only,
--       no SQL change)
--   (2) date-qualified columns -- ("which restock date is this for?")
--   (3) an explicit estimated-vs-actual indicator per date
--   (4) one table, not two -- Item/Current Qty/Avg per day shown once, Item
--       column frozen while scrolling the per-date column groups
--
-- vw_order_reco_combined
--   Self-join pivot of inventory_order_reco's Slot=1 and Slot=2 rows into
--   one row per Item: shared identity columns (Item, Current Qty, Avg per
--   day -- identical across slots, same source row) once, then a "Source N"
--   column per date computed from whether inventory_restock_orders has any
--   uploaded rows for that date (mirrors the TVFs' own is_actual check in
--   031, but as a plain read here -- no water-fill logic, so this stays a
--   clean pass-through per check_grafana_no_logic.py). FULL OUTER JOIN so an
--   item present in only one slot (e.g. newly added between refreshes)
--   still surfaces. `Source 2` is NULL when no second date is registered
--   yet (single-date edge case) rather than a bare 'Estimated'.
--
-- Reads only from 031's inventory_order_reco / vw_order_reco_next_dates and
-- 030's inventory_restock_orders -- the materialized table, the TVFs, and
-- core/order_reco.py's refresh sequence are all untouched.

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_order_reco_combined` AS
WITH
dd AS (
  SELECT MAX(IF(slot=1,delivery_date,NULL)) AS d1,
         MAX(IF(slot=2,delivery_date,NULL)) AS d2
  FROM `jarvis-bhaga-prod.bhaga.vw_order_reco_next_dates`
),
src AS (
  SELECT
    dd.d2,
    (SELECT COUNT(*)>0 FROM `jarvis-bhaga-prod.bhaga.inventory_restock_orders` o
       WHERE o.store='palmetto' AND o.delivery_date=dd.d1) AS actual1,
    (SELECT COUNT(*)>0 FROM `jarvis-bhaga-prod.bhaga.inventory_restock_orders` o
       WHERE o.store='palmetto' AND o.delivery_date=dd.d2) AS actual2
  FROM dd
),
s1 AS (SELECT * FROM `jarvis-bhaga-prod.bhaga.inventory_order_reco` WHERE store='palmetto' AND Slot=1),
s2 AS (SELECT * FROM `jarvis-bhaga-prod.bhaga.inventory_order_reco` WHERE store='palmetto' AND Slot=2)
SELECT
  COALESCE(s1.Item, s2.Item) AS Item,
  COALESCE(s1.`Current Qty`, s2.`Current Qty`) AS `Current Qty`,
  COALESCE(s1.`Avg per day`, s2.`Avg per day`) AS `Avg per day`,
  s1.`On Hand at Restock` AS `On Hand 1`, s1.`Order Tubs` AS `Order Tubs 1`,
  s1.`Order Weight lbs` AS `Order Weight 1`, s1.`After Restock` AS `After Restock 1`,
  s1.`Days Left After Restock` AS `Days Left 1`,
  IF(src.actual1,'Actuals','Estimated') AS `Source 1`,
  s2.`On Hand at Restock` AS `On Hand 2`, s2.`Order Tubs` AS `Order Tubs 2`,
  s2.`Order Weight lbs` AS `Order Weight 2`, s2.`After Restock` AS `After Restock 2`,
  s2.`Days Left After Restock` AS `Days Left 2`,
  IF(src.d2 IS NULL, NULL, IF(src.actual2,'Actuals','Estimated')) AS `Source 2`,
  COALESCE(s1._ord, s2._ord) AS _ord,
  CAST(COALESCE(s1.refreshed_at, s2.refreshed_at) AS STRING) AS refresh_date
FROM s1 FULL OUTER JOIN s2 ON s1.Item = s2.Item
CROSS JOIN src
ORDER BY _ord ASC, `Current Qty` DESC;
