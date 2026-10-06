-- 077: ADP employee unavailability — Issue #337
-- Two sources, one table:
--   status='pending'  — Team Schedule › Pending requests › Unavailability Requests
--                       (one row per request; weekly repeats keep repeat_weekday +
--                       repeat_until instead of being expanded). Replaced wholesale
--                       on every scrape whose requests pane read succeeded.
--   status='approved' — "Unavailable" blocks in the schedule grid, one row per
--                       date. Replaced per scraped week window.
-- ADP only shows unavailability in the grid once a manager approves it, so a
-- pending row is still the employee's stated availability.
-- row_key = status|raw_employee_name|first_date|from_time|to_time (MERGE key).
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c \
--   "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.adp_unavailability` (
  row_key             STRING NOT NULL,
  employee_name       STRING,
  raw_employee_name   STRING,
  status              STRING NOT NULL,
  first_date          DATE NOT NULL,
  from_time           STRING,
  to_time             STRING,
  all_day             BOOL,
  hours               FLOAT64,
  repeat_weekday      INT64,
  repeat_until        DATE,
  expires_at_ct       DATETIME,
  scraped_at_utc      TIMESTAMP,
  materialized_at_utc TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.adp_schedule_requests` (
  request_type        STRING NOT NULL,
  pending             INT64,
  scraped_at_utc      TIMESTAMP,
  materialized_at_utc TIMESTAMP
);
