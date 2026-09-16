-- 071_solo_shift_hours.sql
-- Issue #309: solo-shift premium. Employee x day solo/team hour split derived
-- from adp_punches occupancy, plus the rollups the Labor page and ADP payroll
-- entry read.
--
-- An employee is "solo" for the minutes they are the only person punched in.
-- Occupancy counts every punched employee (including the labor-excluded
-- manager); eligibility for the premium is a separate, later filter.
--
-- Grain is employee x date because that is the grain payroll is entered at.
-- Day-grain totals are exposed as vw_solo_hours_daily rather than duplicated
-- into model_labor_daily: a SUM over this table is exact, and threading punch
-- intervals through build_labor_daily_rows would change three fixed-position
-- sheet headers for a number that is already derivable.
--
-- Money is integer cents (bhaga.mdc invariant 4). premium_cents is authoritative;
-- premium_dollars in the views is a presentation convenience only.
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c \
--   "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.model_solo_hours_daily` (
  date              DATE    NOT NULL,
  employee          STRING  NOT NULL,   -- canonical name, alias-resolved
  solo_minutes      INT64,
  team_minutes      INT64,
  total_minutes     INT64,
  solo_hours        FLOAT64,
  team_hours        FLOAT64,
  total_hours       FLOAT64,
  base_rate_dollars FLOAT64,
  eligible          BOOL,               -- base rate matches the eligible rate AND date >= effective
  premium_cents     INT64,
  materialized_at_utc TIMESTAMP
)
PARTITION BY date;

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
  ROUND(SUM(premium_cents) / 100.0, 2)             AS premium_dollars
FROM `jarvis-bhaga-prod.bhaga.model_solo_hours_daily`
GROUP BY date;

-- Pay-period rollup. This is the view the operator keys into ADP from and the
-- one the payroll draft reads if the ADP rate-2 spike proves out.
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
    s.base_rate_dollars,
    s.eligible,
    s.premium_cents,
    MAX(st.ps) AS period_start
  FROM `jarvis-bhaga-prod.bhaga.model_solo_hours_daily` s
  JOIN period_starts st
    ON st.ps <= s.date
  GROUP BY
    s.date, s.employee, s.solo_minutes, s.team_minutes, s.total_minutes,
    s.base_rate_dollars, s.eligible, s.premium_cents
)
SELECT
  period_start,
  MIN(date)                                        AS first_date,
  MAX(date)                                        AS last_date,
  employee,
  SUM(solo_minutes)                                AS solo_minutes,
  SUM(team_minutes)                                AS team_minutes,
  SUM(total_minutes)                               AS total_minutes,
  ROUND(SUM(solo_minutes) / 60.0, 2)               AS solo_hours,
  ROUND(SUM(team_minutes) / 60.0, 2)               AS team_hours,
  ROUND(SUM(total_minutes) / 60.0, 2)              AS total_hours,
  ANY_VALUE(base_rate_dollars)                     AS base_rate_dollars,
  LOGICAL_OR(eligible)                             AS eligible,
  SUM(premium_cents)                               AS premium_cents,
  ROUND(SUM(premium_cents) / 100.0, 2)             AS premium_dollars
FROM assigned
GROUP BY period_start, employee;
