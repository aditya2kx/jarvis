-- 095: ADP shift changes written from the console + their history — Issue #434
-- adp_shift_changes: the console's one "Apply to ADP" inserts one row per staged shift
--   change (status='queued', one batch_id per Apply, shared with any
--   adp_unavailability_changes rows of the same Apply). The BHAGA_ADP_SCHEDULE_WRITE=apply
--   Cloud Run job applies each in ADP and sets 'applied' | 'skipped' | 'failed'. 'applied'
--   is only written after the job's own read-back of ADP shows the change.
--   kind: open-add | open-assign | open-edit | open-delete
--       | shift-add | shift-edit | shift-delete | shift-reassign | shift-publish (make an ADP draft live)
--   start_min/end_min: the shift as it is in ADP now (the new shift for *-add).
--   to_start_min/to_end_min: new times (*-edit). to_employee: new person (shift-reassign).
--   count: open shifts to create (open-add). employee: the person (assign / shift-*).
-- adp_shift_events: append-only history (never UPDATE/DELETE/MERGE —
--   scripts/check_append_only_history.py); one row per change the job applied.
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c \
--   "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.adp_shift_changes` (
  store          STRING NOT NULL,
  batch_id       STRING NOT NULL,
  change_id      STRING NOT NULL,
  kind           STRING NOT NULL,
  date           DATE NOT NULL,
  employee       STRING,
  start_min      INT64 NOT NULL,
  end_min        INT64 NOT NULL,
  to_start_min   INT64,
  to_end_min     INT64,
  to_employee    STRING,
  count          INT64,
  summary        STRING,
  status         STRING NOT NULL,
  error          STRING,
  adp_name       STRING,
  requested_by   STRING NOT NULL,
  requested_at   TIMESTAMP NOT NULL,
  updated_at     TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.adp_shift_events` (
  store          STRING NOT NULL,
  event_at_utc   TIMESTAMP NOT NULL,
  date           DATE NOT NULL,
  kind           STRING NOT NULL,
  summary        STRING NOT NULL,
  actor          STRING,
  batch_id       STRING,
  change_id      STRING
);
