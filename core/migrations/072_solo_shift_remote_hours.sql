-- 072_solo_shift_remote_hours.sql
-- Issue #309 follow-on: a shift worked away from the shop is not floor coverage.
--
-- Migration 071 counted every punched employee as occupying the shop. A manager
-- working remote therefore made her coworker look accompanied, suppressing a
-- premium that coworker had earned (live 2026-09-07: Tina worked 4.62h alone and
-- was paid base for all of it).
--
-- remote_minutes is a SUBSET of team_minutes, not a fourth bucket:
--   solo_minutes + team_minutes == total_minutes  (bhaga.mdc invariant 2)
-- Remote time can never be solo, so it lands in team and is flagged here so the
-- console can explain why someone with hours has no solo time.
--
-- Which shifts are remote comes from the `solo_shift_remote_days` row in
-- bhaga.store_config (a JSON list of {date, employee}), so annotating a shift is
-- a config edit, never a deploy (user-preferences #29). An unannotated shift is
-- treated as on the floor.
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c \
--   "from core.datastore import ensure_schema; print(ensure_schema())"

ALTER TABLE `jarvis-bhaga-prod.bhaga.model_solo_hours_daily`
  ADD COLUMN IF NOT EXISTS remote_minutes INT64,
  ADD COLUMN IF NOT EXISTS remote_hours   FLOAT64;

-- Day-grain coverage split for the Labor page charts.
-- single_cover_minutes is time the shop ran on one person, counted once for the
-- day regardless of who it was.
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_solo_hours_daily` AS
SELECT
  date,
  SUM(solo_minutes)                                AS single_cover_minutes,
  ROUND(SUM(solo_minutes) / 60.0, 2)               AS single_cover_hours,
  ROUND(SUM(team_minutes) / 60.0, 2)               AS team_hours,
  ROUND(SUM(total_minutes) / 60.0, 2)              AS total_hours,
  COUNT(DISTINCT employee)                         AS headcount,
  COUNTIF(solo_minutes > 0)                        AS employees_with_solo,
  SUM(premium_cents)                               AS premium_cents,
  ROUND(SUM(premium_cents) / 100.0, 2)             AS premium_dollars,
  -- COALESCE: rows materialized before this migration have NULL here, and a
  -- NULL in a SUM would make the whole day's remote total NULL rather than 0.
  SUM(COALESCE(remote_minutes, 0))                 AS remote_minutes,
  ROUND(SUM(COALESCE(remote_minutes, 0)) / 60.0, 2) AS remote_hours,
  ROUND(
    (SUM(total_minutes) - SUM(COALESCE(remote_minutes, 0))) / 60.0, 2
  )                                                AS on_floor_hours
FROM `jarvis-bhaga-prod.bhaga.model_solo_hours_daily`
GROUP BY date;

-- Pay-period rollup. This is the view the operator keys into ADP from and the
-- one the payroll draft reads.
--
-- Days are assigned to the LATEST pay_period_start at or before the date, and
-- pay_period_end is deliberately NOT used as an upper bound. model_labor_period
-- truncates the open period's end to the model's data window (on 2026-09-15 the
-- 2026-09-07 row ended 09-14, not 09-20), so a BETWEEN join silently dropped
-- every solo hour worked after the window — understating the premium the
-- operator would then key into ADP. Bounding by start only is also robust to a
-- future change in pay frequency, since no interval length is assumed.
--
-- first_date / last_date report the punch coverage actually rolled up, so a
-- partial period is visibly partial rather than quietly short.
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_solo_hours_period` AS
WITH period_starts AS (
  SELECT DISTINCT pay_period_start AS ps
  FROM `jarvis-bhaga-prod.bhaga.model_labor_period`
),
assigned AS (
  SELECT
    s.date,
    s.employee,
    s.solo_minutes,
    s.team_minutes,
    s.total_minutes,
    COALESCE(s.remote_minutes, 0) AS remote_minutes,
    s.base_rate_dollars,
    s.eligible,
    s.premium_cents,
    MAX(st.ps) AS period_start
  FROM `jarvis-bhaga-prod.bhaga.model_solo_hours_daily` s
  JOIN period_starts st
    ON st.ps <= s.date
  GROUP BY
    s.date, s.employee, s.solo_minutes, s.team_minutes, s.total_minutes,
    s.remote_minutes, s.base_rate_dollars, s.eligible, s.premium_cents
)
SELECT
  period_start,
  MIN(date)                                        AS first_date,
  MAX(date)                                        AS last_date,
  employee,
  SUM(solo_minutes)                                AS solo_minutes,
  SUM(team_minutes)                                AS team_minutes,
  SUM(total_minutes)                               AS total_minutes,
  SUM(remote_minutes)                              AS remote_minutes,
  ROUND(SUM(solo_minutes) / 60.0, 2)               AS solo_hours,
  ROUND(SUM(team_minutes) / 60.0, 2)               AS team_hours,
  ROUND(SUM(total_minutes) / 60.0, 2)              AS total_hours,
  ROUND(SUM(remote_minutes) / 60.0, 2)             AS remote_hours,
  ROUND((SUM(total_minutes) - SUM(remote_minutes)) / 60.0, 2) AS on_floor_hours,
  ANY_VALUE(base_rate_dollars)                     AS base_rate_dollars,
  LOGICAL_OR(eligible)                             AS eligible,
  SUM(premium_cents)                               AS premium_cents,
  ROUND(SUM(premium_cents) / 100.0, 2)             AS premium_dollars
FROM assigned
GROUP BY period_start, employee;
