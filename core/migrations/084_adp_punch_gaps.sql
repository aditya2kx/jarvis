-- 084: ADP open punches + operator decisions — Issue #356
--
-- adp_timecard_gaps: one row per employee-day the Timecards month view shows
-- with an open entry (clock-in, no clock-out), a missing clock-in, or a
-- scheduled shift with no entry once the day is over. The Timecard XLSX drops
-- open entries entirely, so none of these reach adp_shifts / adp_punches.
-- Written by backfill_from_downloads._load_adp_timecard_gaps: purge the
-- scraped pay_period_start(s), then MERGE on (store, date, employee_id) — a
-- gap fixed in ADP disappears on the next scrape.
--
-- punch_gap_decisions: the operator's Accept / Edit / Reject per gap from the
-- console /labor review table. Append-only per decision_id; the latest
-- decided_at per (store, date, employee_id) is the live one. status tracks the
-- ADP write-back (recorded → pending_write → applying → applied | failed |
-- already_resolved); reject never leaves "rejected".
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c \
--   "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.adp_timecard_gaps` (
  store                 STRING NOT NULL,
  date                  DATE NOT NULL,
  employee_id           STRING NOT NULL,
  raw_employee_name     STRING,
  pay_period_start      DATE,
  kind                  STRING,
  entries_json          STRING,
  scheduled_ranges_json STRING,
  open_entry_index      INT64,
  suggested_in          STRING,   -- no_entry only: the scheduled start
  suggested_out         STRING,
  suggested_hours       FLOAT64,
  rule                  STRING,
  adp_error_flag        BOOL,
  scraped_at_utc        TIMESTAMP,
  materialized_at_utc   TIMESTAMP
);

ALTER TABLE `jarvis-bhaga-prod.bhaga.adp_timecard_gaps`
  ADD COLUMN IF NOT EXISTS suggested_in STRING;

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.punch_gap_decisions` (
  decision_id      STRING NOT NULL,
  store            STRING NOT NULL,
  date             DATE NOT NULL,
  employee_id      STRING NOT NULL,
  action           STRING NOT NULL,
  in_time          STRING,
  out_time         STRING,
  open_entry_index INT64,
  note             STRING,
  status           STRING NOT NULL,
  error            STRING,
  decided_by       STRING,
  decided_at       TIMESTAMP NOT NULL,
  applied_at       TIMESTAMP,
  execution_name   STRING
);
