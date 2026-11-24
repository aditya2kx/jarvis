-- 099_square_location_hours.sql
-- Each store's opening hours from its Square location (business_hours), copied every
-- night by bhaga-daily-refresh (skills/square_api/location_hours.py). The Operator
-- Console's Labor page reads Square live and falls back to the latest copy here; the
-- history shows when the hours changed. One row per (store, as_of_date, day_of_week),
-- MERGEd. A closed day has NULL open_min / close_min.
-- Apply: BHAGA_DATASTORE=bigquery python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.square_location_hours` (
  store         STRING    NOT NULL,
  as_of_date    DATE      NOT NULL,  -- America/Chicago day of the copy
  day_of_week   INT64     NOT NULL,  -- Mon = 0 … Sun = 6
  open_min      INT64,               -- minute of day, local time
  close_min     INT64,
  location_id   STRING    NOT NULL,
  synced_at     TIMESTAMP NOT NULL
);
