-- 073: ADP Team Schedule open (unassigned) shifts — Issue #342
-- One row per open slot. ADP's Open Shifts row shows only a per-day count; the
-- times and paid hours come from the day's focus pane (runner._scrape_open_shifts).
-- ADP footer totals exclude open shifts, so these never overlap
-- adp_scheduled_daily / adp_scheduled_shifts.
--
-- slot_index is the position within the day's pane (0-based). The loader purges
-- every week_start it scraped successfully before upserting, so a slot that was
-- filled or deleted in ADP disappears here too.
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c \
--   "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.adp_open_shifts` (
  date                DATE NOT NULL,
  slot_index          INT64 NOT NULL,
  shift_range         STRING,
  scheduled_hours     FLOAT64,
  week_start          DATE,
  scraped_at_utc      TIMESTAMP,
  materialized_at_utc TIMESTAMP
);
