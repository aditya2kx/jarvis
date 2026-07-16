-- 039: per-employee ADP scheduled shifts (forward Team Schedule grid)
-- Additive to adp_scheduled_daily (store-level day totals from footer).

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.adp_scheduled_shifts` (
  date                DATE NOT NULL,
  employee_id         STRING NOT NULL,
  employee_name       STRING,
  scheduled_hours     FLOAT64,
  shift_ranges_json   STRING,
  week_start          DATE,
  scraped_at_utc      TIMESTAMP,
  materialized_at_utc TIMESTAMP
);
