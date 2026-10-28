-- 092: ADP unavailability written from the console + its history — Issue #427
-- adp_unavailability_changes: the console's Apply to ADP inserts one row per staged
--   change (status='queued', one batch_id per Apply); the BHAGA_ADP_UNAVAIL_WRITE
--   Cloud Run job applies each in ADP and sets 'applied' | 'failed'. 'applied' is only
--   written after the job's own read-back of ADP shows the change.
--   kind: approve | deny | add | edit | delete. scope (edit/delete): date | series.
-- adp_unavailability_events: append-only history (never UPDATE/DELETE/MERGE —
--   scripts/check_append_only_history.py). source='console' rows are written when a
--   change is applied; source='adp_read' rows when a schedule read finds an entry
--   appeared, was approved, expired or disappeared without a console change.
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c \
--   "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.adp_unavailability_changes` (
  store          STRING NOT NULL,
  batch_id       STRING NOT NULL,
  change_id      STRING NOT NULL,
  kind           STRING NOT NULL,
  row_key        STRING,
  employee       STRING NOT NULL,
  scope          STRING,
  first_date     DATE,
  all_day        BOOL,
  from_time      STRING,
  to_time        STRING,
  weekly         BOOL,
  repeat_until   DATE,
  summary        STRING,
  status         STRING NOT NULL,
  error          STRING,
  requested_by   STRING NOT NULL,
  requested_at   TIMESTAMP NOT NULL,
  updated_at     TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.adp_unavailability_events` (
  store          STRING NOT NULL,
  event_at_utc   TIMESTAMP NOT NULL,
  employee       STRING NOT NULL,
  event          STRING NOT NULL,
  summary        STRING NOT NULL,
  source         STRING NOT NULL,
  actor          STRING,
  change_id      STRING,
  row_key        STRING
);
