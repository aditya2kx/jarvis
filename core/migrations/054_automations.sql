-- 054_automations.sql
-- Issue #216: Operator-controllable automations (Team pulse ClickUp posts).
-- Config + post history live in BQ so the Operator Console can edit without redeploy.
-- Scheduler fires daily 08:00 CT; the job no-ops unless enabled and today ∈ days_of_week.
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.automations` (
  store           STRING    NOT NULL,
  automation_id   STRING    NOT NULL,
  enabled         BOOL      NOT NULL,
  days_of_week    STRING    NOT NULL,  -- JSON list of Python weekdays Mon=0..Sun=6
  hour_local      INT64     NOT NULL,
  minute_local    INT64     NOT NULL,
  timezone        STRING    NOT NULL,
  destination     STRING    NOT NULL,  -- 'dm' | 'channel'
  channel_id      STRING,
  dm_user_id      STRING,
  workspace_id    STRING    NOT NULL,
  template        STRING    NOT NULL,
  updated_at      TIMESTAMP,
  updated_by      STRING
);

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.automation_posts` (
  store           STRING    NOT NULL,
  automation_id   STRING    NOT NULL,
  post_date_ct    DATE      NOT NULL,
  posted_at       TIMESTAMP NOT NULL,
  destination     STRING    NOT NULL,
  channel_id      STRING,
  message_id      STRING,
  content         STRING,
  dry_run         BOOL      NOT NULL,
  trigger         STRING    NOT NULL,  -- scheduler | once | preview
  updated_by      STRING
);
