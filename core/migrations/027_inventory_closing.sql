-- 027_inventory_closing.sql
-- Adds the inventory_closing_daily table for the Order Assistant capability
-- (Issue #113, slice A).  Stores parsed closing-form inventory readings from
-- ClickUp, starting with the 8 HQ bases.  Scalable to any category via the
-- 'category' column — new categories require only config changes, not schema
-- migrations.
--
-- Natural key for MERGE upsert: (store, source_task_id, field_id)
-- This deduplicates duplicate display-name fields (e.g. two 'Mango' fields
-- with distinct ClickUp field IDs) and makes reruns idempotent.
--
-- The vw_inventory_base_latest_daily view is the Grafana query target:
-- it resolves to one row per (store, submitted_date, item) — the latest
-- submission on that date — so panels always show the most recent reading.
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c
--   "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.inventory_closing_daily` (
  store           STRING    NOT NULL,   -- e.g. 'palmetto'; multi-store from day 1
  submitted_date  DATE      NOT NULL,   -- America/Chicago date of the closing shift
  submitted_ts    TIMESTAMP,            -- exact submission timestamp (UTC), from task name
  source_task_id  STRING    NOT NULL,   -- ClickUp task id; part of natural key + audit
  category        STRING    NOT NULL,   -- 'base' now; 'milk', 'supply', … later
  item            STRING    NOT NULL,   -- canonical display name e.g. 'Açaí'
  field_id        STRING    NOT NULL,   -- ClickUp custom-field id; dedupes same-name fields
  field_name      STRING,               -- raw ClickUp display name (audit only)
  raw_text        STRING,               -- original free-text e.g. '33+30%' (never lost)
  quantity_units  FLOAT64,              -- normalized value e.g. 33.30 (NULL when unparseable)
  unit            STRING,               -- canonical unit label e.g. 'tubs'
  parse_ok        BOOL,                 -- false when quantity_units is NULL (distinguishes 0)
  run_id          STRING,               -- pipeline lineage (from daily_refresh run_id)
  scraped_at_utc  TIMESTAMP             -- ingest timestamp
) PARTITION BY submitted_date;

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_inventory_base_latest_daily` AS
SELECT * EXCEPT (rn)
FROM (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY store, submitted_date, item
      ORDER BY submitted_ts DESC
    ) AS rn
  FROM `jarvis-bhaga-prod.bhaga.inventory_closing_daily`
  WHERE category = 'base'
) t
WHERE rn = 1;
