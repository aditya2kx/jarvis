-- 089: Daily missing-punch check in ClickUp Shift Coverage & Trades — Issue #397
--
-- employee_contacts: ADP profile email(s) per canonical employee, captured by
-- the nightly pay_info profile visit. MERGE on (store, employee_id); a scrape
-- that finds no address writes no row, so a stored email is never blanked.
-- Matches staff to ClickUp members (same email in ADP and ClickUp) for @tags
-- and for reading who replied.
--
-- punch_check_items: one row per gap listed in a daily post (message_id is the
-- ClickUp message) — the reply sweep matches a reply's author to these.
--
-- punch_check_replies: every thread reply the sweep has read, once (MERGE on
-- reply_id). outcome: recorded | queued | applying | done | failed |
-- not_understood | unknown_author | nothing_open. decision_ids_json links the
-- punch_gap_decisions rows; reacted_at is set when the ✅ lands.
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c \
--   "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.employee_contacts` (
  store           STRING NOT NULL,
  employee_id     STRING NOT NULL,
  emails_json     STRING,
  source          STRING,
  scraped_at_utc  TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.punch_check_items` (
  store            STRING NOT NULL,
  post_date_ct     DATE NOT NULL,
  message_id       STRING NOT NULL,
  channel_id       STRING,
  date             DATE NOT NULL,
  employee_id      STRING NOT NULL,
  kind             STRING,
  clickup_user_id  STRING,
  posted_at        TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.punch_check_replies` (
  store              STRING NOT NULL,
  reply_id           STRING NOT NULL,
  message_id         STRING,
  author_id          STRING,
  employee_id        STRING,
  text               STRING,
  outcome            STRING,
  detail             STRING,
  decision_ids_json  STRING,
  processed_at       TIMESTAMP,
  reacted_at         TIMESTAMP
);
