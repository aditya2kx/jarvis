-- 089: ADP open shifts the operator assigned from the console — Issue #412
-- The Labor "Write to ADP" click inserts one row per selected open shift
-- (status='queued', one assign_id per click); agents/bhaga/scripts/adp_schedule_write.py
-- --assign-id assigns each existing ADP open shift to that person and sets status to
-- 'assigned' | 'skipped' | 'failed'. Separate from labor_schedule_pushes so an
-- assignment never turns a week into a saved (locked) draft plan.
-- row_key = store|date|start_min|end_min|employee; a row_key already 'assigned' is skipped.
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c \
--   "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.labor_open_shift_assignments` (
  store         STRING NOT NULL,
  assign_id     STRING NOT NULL,
  row_key       STRING NOT NULL,
  date          DATE NOT NULL,
  employee      STRING NOT NULL,
  start_min     INT64 NOT NULL,
  end_min       INT64 NOT NULL,
  status        STRING NOT NULL,
  error         STRING,
  adp_name      STRING,
  requested_by  STRING NOT NULL,
  requested_at  TIMESTAMP NOT NULL,
  updated_at    TIMESTAMP
);
