-- 056: Square order fulfillment + ops-clock columns on square_transactions
-- Scheduled 3P orders place overnight (created_at) but fulfill in the morning.
-- Sales Hour Aggregation buckets on ops_at_* (promised pickup/deliver slot),
-- not place-time. Raw fulfillment timestamps kept for audit / Order Quality joins.

ALTER TABLE `jarvis-bhaga-prod.bhaga.square_transactions`
ADD COLUMN IF NOT EXISTS fulfillment_type STRING;

ALTER TABLE `jarvis-bhaga-prod.bhaga.square_transactions`
ADD COLUMN IF NOT EXISTS schedule_type STRING;

ALTER TABLE `jarvis-bhaga-prod.bhaga.square_transactions`
ADD COLUMN IF NOT EXISTS pickup_at_utc STRING;

ALTER TABLE `jarvis-bhaga-prod.bhaga.square_transactions`
ADD COLUMN IF NOT EXISTS deliver_at_utc STRING;

ALTER TABLE `jarvis-bhaga-prod.bhaga.square_transactions`
ADD COLUMN IF NOT EXISTS courier_pickup_at_utc STRING;

ALTER TABLE `jarvis-bhaga-prod.bhaga.square_transactions`
ADD COLUMN IF NOT EXISTS ready_at_utc STRING;

ALTER TABLE `jarvis-bhaga-prod.bhaga.square_transactions`
ADD COLUMN IF NOT EXISTS picked_up_at_utc STRING;

ALTER TABLE `jarvis-bhaga-prod.bhaga.square_transactions`
ADD COLUMN IF NOT EXISTS delivered_at_utc STRING;

ALTER TABLE `jarvis-bhaga-prod.bhaga.square_transactions`
ADD COLUMN IF NOT EXISTS placed_at_utc STRING;

ALTER TABLE `jarvis-bhaga-prod.bhaga.square_transactions`
ADD COLUMN IF NOT EXISTS accepted_at_utc STRING;

ALTER TABLE `jarvis-bhaga-prod.bhaga.square_transactions`
ADD COLUMN IF NOT EXISTS closed_at_utc STRING;

ALTER TABLE `jarvis-bhaga-prod.bhaga.square_transactions`
ADD COLUMN IF NOT EXISTS ops_at_local_iso STRING;

ALTER TABLE `jarvis-bhaga-prod.bhaga.square_transactions`
ADD COLUMN IF NOT EXISTS ops_date_local DATE;

ALTER TABLE `jarvis-bhaga-prod.bhaga.square_transactions`
ADD COLUMN IF NOT EXISTS ops_hour_local INT64;
