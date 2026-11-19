-- 098_plaid_categorize_jobs.sql
-- Accounting categorization jobs + append-only rule history (Accounting bugs, Nov 2026).
--
-- plaid_categorize_jobs: one row per console Reapply / rule commit / revert. The console
-- polls it for progress (done / total) and refuses a rule commit while a Reapply is
-- running (heartbeat_at within the stale window), so two writers never race on
-- plaid_transactions. Status rows are updated in place (running → done | failed).
--
-- plaid_category_rule_events: append-only snapshot of every rule create / enable /
-- disable / revert (who, when, full rule JSON). Never UPDATE / DELETE / MERGE it
-- (scripts/check_append_only_history.py).
-- Apply: BHAGA_DATASTORE=bigquery python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.plaid_categorize_jobs` (
  job_id            STRING    NOT NULL,
  kind              STRING    NOT NULL,  -- 'reapply' | 'revert_rule'
  status            STRING    NOT NULL,  -- 'running' | 'done' | 'failed'
  rule_id           STRING,
  total             INT64,
  done              INT64,
  updated           INT64,
  unchanged         INT64,
  skipped_override  INT64,
  message           STRING,
  started_by        STRING,
  started_at        TIMESTAMP NOT NULL,
  heartbeat_at      TIMESTAMP,
  finished_at       TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.plaid_category_rule_events` (
  event_id    STRING    NOT NULL,
  rule_id     STRING    NOT NULL,
  action      STRING    NOT NULL,  -- 'create' | 'enable' | 'disable' | 'revert'
  rule_json   STRING,              -- full rule row after the change
  note        STRING,
  by_email    STRING,
  event_at    TIMESTAMP NOT NULL
);
