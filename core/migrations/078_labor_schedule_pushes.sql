-- 078: Draft shifts pushed to ADP Team Schedule — Issue #337
-- The console "Save to ADP as drafts" click inserts one row per draft shift
-- (status='queued', one push_id per click); agents/bhaga/scripts/adp_schedule_write.py
-- creates each in ADP (Create shift / Create open shift → Save as draft) and
-- updates status to 'drafted' | 'skipped' | 'failed'. "Publish week" flips the
-- week's 'drafted' rows to 'published' after ADP's Publish drafts succeeds.
-- row_key = store|date|employee-or-OPEN|start_min|end_min; a row_key already
-- 'drafted' or 'published' is skipped so a double click never duplicates a shift.
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c \
--   "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.labor_schedule_pushes` (
  store         STRING NOT NULL,
  push_id       STRING NOT NULL,
  week_start    DATE NOT NULL,
  row_key       STRING NOT NULL,
  date          DATE NOT NULL,
  employee      STRING,
  start_min     INT64 NOT NULL,
  end_min       INT64 NOT NULL,
  status        STRING NOT NULL,
  error         STRING,
  requested_by  STRING NOT NULL,
  requested_at  TIMESTAMP NOT NULL,
  updated_at    TIMESTAMP
);
