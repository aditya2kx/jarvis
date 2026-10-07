-- 087_monthly_recognition.sql
-- Issue #369: Monthly recognition (MVP / High Five) drafts + Square gift cards.
-- ClickUp chat is copied into BQ so the Operator Console page loads from BQ, not live API;
-- recognition_gift_cards is the idempotency ledger for real-money Square gift cards.
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

-- MERGE key (channel_id, message_id). parent_message_id NULL = top-level message.
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.clickup_chat_messages` (
  store             STRING    NOT NULL,
  channel_id        STRING    NOT NULL,
  message_id        STRING    NOT NULL,
  parent_message_id STRING,
  user_id           STRING,
  posted_at         TIMESTAMP NOT NULL,
  content           STRING,
  reply_count       INT64,
  synced_at         TIMESTAMP NOT NULL
);

-- MERGE key (workspace_id, user_id).
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.clickup_members` (
  workspace_id STRING    NOT NULL,
  user_id      STRING    NOT NULL,
  username     STRING,
  email        STRING,
  synced_at    TIMESTAMP NOT NULL
);

-- Append-only: one row per sync attempt (start), one per finish.
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.clickup_sync_runs` (
  store             STRING    NOT NULL,
  run_id            STRING    NOT NULL,
  trigger           STRING    NOT NULL,  -- console | auto | nightly
  started_at        TIMESTAMP NOT NULL,
  finished_at       TIMESTAMP,
  status            STRING    NOT NULL,  -- running | ok | failed
  messages_upserted INT64,
  members_upserted  INT64,
  error             STRING,
  updated_by        STRING
);

-- MERGE key (idempotency_key) = rec-<store>-<award_month>-<clickup_user_id>-<card_index>.
-- The full gift card number (GAN) is never stored — only the last 4.
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.recognition_gift_cards` (
  store               STRING    NOT NULL,
  award_month         STRING    NOT NULL,  -- 'YYYY-MM' or 'test-<stamp>'
  idempotency_key     STRING    NOT NULL,
  clickup_user_id     STRING    NOT NULL,
  award               STRING    NOT NULL,  -- MVP | High Five | Test
  recipient_first     STRING,
  recipient_last      STRING,
  recipient_email     STRING,
  card_index          INT64     NOT NULL,
  amount_cents        INT64     NOT NULL,
  location_id         STRING    NOT NULL,
  square_gift_card_id STRING,
  gan_last4           STRING,
  status              STRING    NOT NULL,  -- pending | created | activated | emailed | failed
  email_message_id    STRING,
  error               STRING,
  approved_by         STRING,
  created_at          TIMESTAMP NOT NULL,
  updated_at          TIMESTAMP NOT NULL
);
