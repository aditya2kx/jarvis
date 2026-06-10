-- 013_adp_scheduled_hours.sql
-- ADP RUN Team Schedule: per-day SCHEDULED labor hours (forward-looking).
-- Applied via: python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

-- Table: one row per scheduled day, scraped from ADP's "Manage Schedules" grid
-- (skills/adp_run_automation/schedule_backend.py). Forward-looking: the nightly
-- scrape captures the current + next week, so rows for upcoming dates are
-- continuously refreshed (idempotent upsert on date).
-- merge_keys (runtime): ["date"]
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.adp_scheduled_daily` (
  date                  DATE NOT NULL,
  scheduled_hours       FLOAT64,   -- decimal hours scheduled across all employees that day
  employee_count        INT64,     -- # employees scheduled that day
  week_start            DATE,      -- Monday of the ADP schedule week the row came from
  scraped_at_utc        TIMESTAMP,
  materialized_at_utc   TIMESTAMP
);

-- Scheduled-vs-goal view: pairs each scheduled day with the forecast row that
-- drives goal hours. Goal hours themselves are computed in Grafana as
-- forecast_items * $goal_hours_per_item (the same template var panel 71 uses),
-- so this view just surfaces the inputs (scheduled_hours, forecast_items,
-- forecast_orders) plus actual worked hours when the day is in the past.
-- One row per scheduled day from today forward (the actionable horizon).
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_scheduled_vs_goal` AS
SELECT
  s.date,
  s.scheduled_hours,
  s.employee_count,
  f.forecast_orders,
  f.forecast_items,
  a.total_hours        AS actual_hours
FROM `jarvis-bhaga-prod.bhaga.adp_scheduled_daily` s
LEFT JOIN `jarvis-bhaga-prod.bhaga.model_forecast_daily` f
  ON f.date = s.date
LEFT JOIN `jarvis-bhaga-prod.bhaga.vw_model_labor_daily` a
  ON a.date = s.date
WHERE s.date >= DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL 7 DAY)
ORDER BY s.date;
