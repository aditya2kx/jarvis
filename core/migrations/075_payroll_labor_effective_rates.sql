-- 075_payroll_labor_effective_rates.sql
-- Issue #343: price every shift at the rate in effect on its date
-- (vw_wage_rate_effective, 074) instead of today's single adp_wage_rates rate,
-- so a raise never reprices closed pay periods.
--
-- Payroll: shifts are split into rate segments per period; est_wages sums
-- each segment's regular + OT pay. wage_rate_dollars shows the rate as of
-- period_end. A period with any unpriced segment (no history row) falls back
-- to hours × that displayed rate, which for an employee with only the 074
-- seed row is exactly the pre-075 formula.
-- Live labor views: same join, rate per shift date; flags stay on
-- adp_wage_rates.
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c
--   "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_model_payroll_period` AS
WITH earn AS (
  SELECT
    period_start,
    period_end,
    employee,
    SUM(IF(description IN (
        'Regular', 'Overtime', 'Double Overtime', 'Holiday', 'Salary'
      ), amount, 0))                                                              AS adp_wages_paid,
    SUM(IF(description = 'Bonus', amount, 0))                                    AS adp_bonus_paid,
    SUM(IF(description = 'Credit Card Tips Owed', amount, 0))                    AS adp_tips_paid,
    SUM(IF(
        description NOT LIKE '%reimbursement%'
        AND description NOT LIKE '%Cash tips%',
      amount, 0))                                                                  AS adp_total_paid
  FROM `jarvis-bhaga-prod.bhaga.adp_earnings`
  GROUP BY period_start, period_end, employee
),
rec AS (
  SELECT
    COALESCE(
      SAFE.PARSE_DATE('%Y-%m-%d', SPLIT(pay_period, '..')[SAFE_OFFSET(0)]),
      SAFE.PARSE_DATE('%Y-%m-%d', pay_period)
    ) AS period_start,
    employee,
    ROUND(SUM(amount_cents) / 100.0, 2) AS recognition_bonus,
    STRING_AGG(
      NULLIF(TRIM(reason), ''),
      '; '
      ORDER BY updated_at
    ) AS recognition_reason
  FROM `jarvis-bhaga-prod.bhaga.recognition_bonuses`
  WHERE COALESCE(
      SAFE.PARSE_DATE('%Y-%m-%d', SPLIT(pay_period, '..')[SAFE_OFFSET(0)]),
      SAFE.PARSE_DATE('%Y-%m-%d', pay_period)
    ) IS NOT NULL
  GROUP BY period_start, employee
),
excl AS (
  SELECT TRIM(n) AS employee
  FROM `jarvis-bhaga-prod.bhaga.store_config`
  CROSS JOIN UNNEST(SPLIT(value, ';')) AS n
  WHERE key = 'excluded_from_tip_pool' AND TRIM(n) != ''
),
periods AS (
  SELECT DISTINCT
    period_start,
    period_end,
    is_open,
    CASE
      WHEN is_open THEN LEAST(
        DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL 1 DAY),
        DATE_ADD(period_start, INTERVAL 13 DAY)
      )
      ELSE period_end
    END AS hours_end
  FROM `jarvis-bhaga-prod.bhaga.model_tip_alloc_period`
),
perks AS (
  SELECT
    p.period_start,
    p.period_end,
    e.employee,
    ROUND(SUM(e.amount_cents) / 100.0, 2) AS perks,
    STRING_AGG(
      CONCAT(e.perk_id, ':', CAST(ROUND(e.amount_cents / 100.0, 2) AS STRING)),
      ';'
      ORDER BY e.perk_id
    ) AS perk_reason
  FROM periods p
  INNER JOIN `jarvis-bhaga-prod.bhaga.employee_perks` e
    ON IFNULL(e.pay_period, '') = ''
    OR e.pay_period = CONCAT(
      CAST(p.period_start AS STRING), '..', CAST(p.period_end AS STRING)
    )
  GROUP BY p.period_start, p.period_end, e.employee
),
seg AS (
  SELECT
    p.period_start,
    p.period_end,
    p.is_open,
    s.canonical_name AS employee,
    er.wage_rate_dollars,
    er.ot_rate_dollars,
    ROUND(SUM(
        COALESCE(
        NULLIF(s.total_hours, 0),
        TIME_DIFF(
          COALESCE(
            SAFE.PARSE_TIME('%H:%M', s.out_time),
            SAFE.PARSE_TIME('%H:%M:%S', s.out_time)
          ),
          COALESCE(
            SAFE.PARSE_TIME('%H:%M', s.in_time),
            SAFE.PARSE_TIME('%H:%M:%S', s.in_time)
          ),
          MINUTE
        ) / 60.0,
        0
      )
    ), 2) AS hours_worked,
    ROUND(SUM(COALESCE(s.ot_hours, 0)), 2) AS ot_hours
  FROM periods p
  INNER JOIN `jarvis-bhaga-prod.bhaga.adp_shifts` s
    ON s.date BETWEEN p.period_start AND p.hours_end
   AND IFNULL(s.canonical_name, '') != ''
  LEFT JOIN `jarvis-bhaga-prod.bhaga.vw_wage_rate_effective` er
    ON er.employee_id = s.canonical_name
   AND s.date BETWEEN er.effective_from AND er.effective_to
  GROUP BY p.period_start, p.period_end, p.is_open, s.canonical_name,
           er.wage_rate_dollars, er.ot_rate_dollars
),
shift_hours AS (
  SELECT
    period_start,
    period_end,
    is_open,
    employee,
    ROUND(SUM(hours_worked), 2) AS hours_worked,
    ROUND(SUM(ot_hours), 2) AS ot_hours,
    IF(
      COUNTIF(wage_rate_dollars IS NULL) > 0,
      CAST(NULL AS NUMERIC),
      SUM(
        CAST(GREATEST(hours_worked - ot_hours, 0) AS NUMERIC)
          * CAST(wage_rate_dollars AS NUMERIC)
        + CAST(ot_hours AS NUMERIC)
          * COALESCE(
              CAST(ot_rate_dollars AS NUMERIC),
              CAST(wage_rate_dollars AS NUMERIC) * 1.5
            )
      )
    ) AS est_wages
  FROM seg
  GROUP BY period_start, period_end, is_open, employee
),
tip_rows AS (
  SELECT
    t.period_start,
    t.period_end,
    t.is_open,
    t.employee,
    COALESCE(sh.hours_worked, t.hours_worked) AS hours_worked,
    COALESCE(sh.ot_hours, 0) AS ot_hours,
    t.our_calc AS tips_allocated,
    'Part-time' AS labor_type,
    sh.est_wages
  FROM `jarvis-bhaga-prod.bhaga.model_tip_alloc_period` t
  LEFT JOIN shift_hours sh
    ON t.period_start = sh.period_start
   AND t.period_end = sh.period_end
   AND t.employee = sh.employee
),
window_people AS (
  SELECT DISTINCT
    p.period_start,
    p.period_end,
    p.is_open,
    s.canonical_name AS employee
  FROM periods p
  INNER JOIN `jarvis-bhaga-prod.bhaga.adp_shifts` s
    ON s.date BETWEEN p.period_start AND p.period_end
   AND IFNULL(s.canonical_name, '') != ''
),
punch_rows AS (
  SELECT
    wp.period_start,
    wp.period_end,
    wp.is_open,
    wp.employee,
    COALESCE(sh.hours_worked, 0) AS hours_worked,
    COALESCE(sh.ot_hours, 0) AS ot_hours,
    CAST(0 AS FLOAT64) AS tips_allocated,
    CASE
      WHEN e.employee IS NOT NULL
        OR IFNULL(w.is_salaried, FALSE)
        OR IFNULL(w.excluded_from_labor_pct, FALSE)
      THEN 'Full-time'
      ELSE 'Part-time'
    END AS labor_type,
    sh.est_wages
  FROM window_people wp
  LEFT JOIN shift_hours sh
    ON wp.period_start = sh.period_start
   AND wp.period_end = sh.period_end
   AND wp.employee = sh.employee
  LEFT JOIN `jarvis-bhaga-prod.bhaga.adp_wage_rates` w
    ON wp.employee = w.canonical_name
  LEFT JOIN excl e
    ON wp.employee = e.employee
  WHERE NOT EXISTS (
    SELECT 1
    FROM `jarvis-bhaga-prod.bhaga.model_tip_alloc_period` t
    WHERE t.period_start = wp.period_start
      AND t.period_end = wp.period_end
      AND t.employee = wp.employee
  )
),
rates AS (
  SELECT
    canonical_name,
    ANY_VALUE(is_salaried) AS is_salaried,
    ANY_VALUE(excluded_from_labor_pct) AS excluded_from_labor_pct
  FROM `jarvis-bhaga-prod.bhaga.adp_wage_rates`
  WHERE IFNULL(canonical_name, '') != ''
  GROUP BY canonical_name
),
carry_rows AS (
  SELECT
    p.period_start,
    p.period_end,
    p.is_open,
    w.canonical_name AS employee,
    CAST(0 AS FLOAT64) AS hours_worked,
    CAST(0 AS FLOAT64) AS ot_hours,
    CAST(0 AS FLOAT64) AS tips_allocated,
    CASE
      WHEN e.employee IS NOT NULL
        OR IFNULL(w.is_salaried, FALSE)
        OR IFNULL(w.excluded_from_labor_pct, FALSE)
      THEN 'Full-time'
      ELSE 'Part-time'
    END AS labor_type,
    CAST(NULL AS NUMERIC) AS est_wages
  FROM periods p
  CROSS JOIN rates w
  LEFT JOIN excl e
    ON w.canonical_name = e.employee
  WHERE EXISTS (
    SELECT 1
    FROM `jarvis-bhaga-prod.bhaga.adp_shifts` s
    WHERE s.canonical_name = w.canonical_name
      AND s.date >= DATE_SUB(p.period_start, INTERVAL 28 DAY)
      AND s.date < p.period_start
  )
    AND NOT EXISTS (
      SELECT 1 FROM tip_rows t
      WHERE t.period_start = p.period_start AND t.employee = w.canonical_name
    )
    AND NOT EXISTS (
      SELECT 1 FROM window_people u
      WHERE u.period_start = p.period_start AND u.employee = w.canonical_name
    )
),
roster AS (
  SELECT * FROM tip_rows
  UNION ALL
  SELECT * FROM punch_rows
  UNION ALL
  SELECT * FROM carry_rows
),
rate_at_end AS (
  SELECT
    p.period_start,
    p.period_end,
    er.employee_id AS employee,
    er.wage_rate_dollars,
    er.ot_rate_dollars
  FROM periods p
  INNER JOIN `jarvis-bhaga-prod.bhaga.vw_wage_rate_effective` er
    ON p.period_end BETWEEN er.effective_from AND er.effective_to
),
priced AS (
  SELECT
    r.*,
    COALESCE(rae.wage_rate_dollars, w.wage_rate_dollars) AS wage_rate_dollars,
    IF(rae.wage_rate_dollars IS NOT NULL, rae.ot_rate_dollars, w.ot_rate_dollars) AS ot_rate_dollars
  FROM roster r
  LEFT JOIN rate_at_end rae
    ON r.period_start = rae.period_start
   AND r.period_end = rae.period_end
   AND r.employee = rae.employee
  LEFT JOIN `jarvis-bhaga-prod.bhaga.adp_wage_rates` w
    ON r.employee = w.canonical_name
),
wages AS (
  SELECT
    p.*,
    CASE
      WHEN p.est_wages IS NOT NULL THEN p.est_wages
      WHEN p.wage_rate_dollars IS NULL THEN CAST(NULL AS NUMERIC)
      ELSE
        CAST(GREATEST(p.hours_worked - COALESCE(p.ot_hours, 0), 0) AS NUMERIC)
          * CAST(p.wage_rate_dollars AS NUMERIC)
        + CAST(COALESCE(p.ot_hours, 0) AS NUMERIC)
          * COALESCE(
              CAST(p.ot_rate_dollars AS NUMERIC),
              CAST(p.wage_rate_dollars AS NUMERIC) * 1.5
            )
    END AS gross
  FROM priced p
)
SELECT
  r.period_start,
  r.period_end,
  r.is_open,
  r.employee,
  r.labor_type,
  r.wage_rate_dollars,
  r.ot_rate_dollars,
  r.hours_worked,
  r.ot_hours,
  CAST(ROUND(r.gross, 2) AS FLOAT64)                                                AS est_gross_pay,
  r.tips_allocated,
  COALESCE(rev.total_bonus, 0)                                                       AS review_bonus,
  COALESCE(rec.recognition_bonus, 0)                                               AS recognition_bonus,
  rec.recognition_reason,
  COALESCE(pk.perks, 0)                                                            AS perks,
  pk.perk_reason,
  CAST(ROUND(
    COALESCE(r.gross, CAST(0 AS NUMERIC))
    + CAST(COALESCE(r.tips_allocated, 0) AS NUMERIC)
    + CAST(COALESCE(rev.total_bonus, 0) AS NUMERIC)
    + CAST(COALESCE(rec.recognition_bonus, 0) AS NUMERIC)
    + CAST(COALESCE(pk.perks, 0) AS NUMERIC),
  2) AS FLOAT64)                                                                    AS est_total_pay,
  e.adp_wages_paid,
  e.adp_tips_paid,
  e.adp_bonus_paid,
  e.adp_total_paid,
  CAST(ROUND(
    r.gross - CAST(COALESCE(e.adp_wages_paid, 0) AS NUMERIC),
  2) AS FLOAT64)                                                                    AS wage_diff,
  ROUND(r.tips_allocated - COALESCE(e.adp_tips_paid, t_paid.adp_paid), 2)          AS tip_diff,
  ROUND(
    COALESCE(rev.total_bonus, 0) + COALESCE(rec.recognition_bonus, 0)
    - COALESCE(e.adp_bonus_paid, 0),
  2)                                                                                AS bonus_diff
FROM wages r
LEFT JOIN `jarvis-bhaga-prod.bhaga.model_review_bonus_period` rev
  ON r.period_start = rev.period_start
 AND r.period_end = rev.period_end
 AND r.employee = rev.employee
LEFT JOIN rec
  ON r.period_start = rec.period_start AND r.employee = rec.employee
LEFT JOIN earn e
  ON r.period_start = e.period_start
 AND r.period_end  = e.period_end
 AND r.employee    = e.employee
LEFT JOIN perks pk
  ON r.period_start = pk.period_start
 AND r.period_end = pk.period_end
 AND r.employee = pk.employee
LEFT JOIN `jarvis-bhaga-prod.bhaga.model_tip_alloc_period` t_paid
  ON r.period_start = t_paid.period_start
 AND r.period_end = t_paid.period_end
 AND r.employee = t_paid.employee;

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_labor_daily_live` AS
WITH live_labor AS (
  SELECT
    s.date,
    SUM(IF(
      IFNULL(w.is_salaried, FALSE) OR IFNULL(w.excluded_from_labor_pct, FALSE),
      0,
      s.total_hours
    )) AS hourly_hours,
    SUM(IF(
      IFNULL(w.is_salaried, FALSE) OR IFNULL(w.excluded_from_labor_pct, FALSE),
      s.total_hours,
      0
    )) AS fulltime_hours,
    SUM(IF(
      IFNULL(w.is_salaried, FALSE) OR IFNULL(w.excluded_from_labor_pct, FALSE),
      0,
      s.total_hours * IFNULL(COALESCE(er.wage_rate_dollars, w.wage_rate_dollars), 0)
    )) AS hourly_labor_cost,
    SUM(IF(
      IFNULL(w.is_salaried, FALSE) OR IFNULL(w.excluded_from_labor_pct, FALSE),
      s.total_hours * IFNULL(COALESCE(er.wage_rate_dollars, w.wage_rate_dollars), 0),
      0
    )) AS fulltime_labor_cost
  FROM `jarvis-bhaga-prod.bhaga.adp_shifts` s
  LEFT JOIN `jarvis-bhaga-prod.bhaga.adp_wage_rates` w
    ON w.employee_id = s.employee_id
  LEFT JOIN `jarvis-bhaga-prod.bhaga.vw_wage_rate_effective` er
    ON er.employee_id = s.employee_id
   AND s.date BETWEEN er.effective_from AND er.effective_to
  WHERE IFNULL(s.total_hours, 0) > 0
  GROUP BY s.date
)
SELECT
  COALESCE(m.date, l.date) AS date,
  m.dow,
  COALESCE(m.net_sales, 0) AS net_sales,
  COALESCE(m.orders, 0) AS orders,
  COALESCE(m.items_sold, 0) AS items_sold,
  COALESCE(l.hourly_hours, 0) AS hourly_hours,
  COALESCE(l.fulltime_hours, 0) AS fulltime_hours,
  COALESCE(l.hourly_hours, 0) + COALESCE(l.fulltime_hours, 0) AS total_hours,
  COALESCE(l.hourly_labor_cost, 0) AS hourly_labor_cost,
  COALESCE(l.fulltime_labor_cost, 0) AS fulltime_labor_cost,
  COALESCE(l.hourly_labor_cost, 0) + COALESCE(l.fulltime_labor_cost, 0) AS total_labor_cost,
  SAFE_DIVIDE(
    COALESCE(l.hourly_labor_cost, 0) + COALESCE(l.fulltime_labor_cost, 0),
    NULLIF(m.net_sales, 0)
  ) AS labor_pct,
  SAFE_DIVIDE(COALESCE(l.hourly_labor_cost, 0), NULLIF(m.net_sales, 0)) AS hourly_pct,
  SAFE_DIVIDE(COALESCE(l.fulltime_labor_cost, 0), NULLIF(m.net_sales, 0)) AS fulltime_pct,
  SAFE_DIVIDE(
    COALESCE(l.hourly_hours, 0) + COALESCE(l.fulltime_hours, 0),
    NULLIF(m.items_sold, 0)
  ) AS hours_per_item,
  SAFE_DIVIDE(COALESCE(l.hourly_hours, 0), NULLIF(m.items_sold, 0)) AS hourly_hours_per_item,
  SAFE_DIVIDE(COALESCE(l.fulltime_hours, 0), NULLIF(m.items_sold, 0)) AS fulltime_hours_per_item,
  SAFE_DIVIDE(m.net_sales, NULLIF(m.orders, 0)) AS avg_order_price
FROM `jarvis-bhaga-prod.bhaga.model_labor_daily` m
FULL OUTER JOIN live_labor l
  ON m.date = l.date;

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_labor_weekly_live` AS
WITH live_labor AS (
  SELECT
    s.date,
    SUM(IF(
      IFNULL(w.is_salaried, FALSE) OR IFNULL(w.excluded_from_labor_pct, FALSE),
      0,
      s.total_hours
    )) AS hourly_hours,
    SUM(IF(
      IFNULL(w.is_salaried, FALSE) OR IFNULL(w.excluded_from_labor_pct, FALSE),
      s.total_hours,
      0
    )) AS fulltime_hours,
    SUM(IF(
      IFNULL(w.is_salaried, FALSE) OR IFNULL(w.excluded_from_labor_pct, FALSE),
      0,
      s.total_hours * IFNULL(COALESCE(er.wage_rate_dollars, w.wage_rate_dollars), 0)
    )) AS hourly_labor_cost,
    SUM(IF(
      IFNULL(w.is_salaried, FALSE) OR IFNULL(w.excluded_from_labor_pct, FALSE),
      s.total_hours * IFNULL(COALESCE(er.wage_rate_dollars, w.wage_rate_dollars), 0),
      0
    )) AS fulltime_labor_cost
  FROM `jarvis-bhaga-prod.bhaga.adp_shifts` s
  LEFT JOIN `jarvis-bhaga-prod.bhaga.adp_wage_rates` w
    ON w.employee_id = s.employee_id
  LEFT JOIN `jarvis-bhaga-prod.bhaga.vw_wage_rate_effective` er
    ON er.employee_id = s.employee_id
   AND s.date BETWEEN er.effective_from AND er.effective_to
  WHERE IFNULL(s.total_hours, 0) > 0
  GROUP BY s.date
),
daily AS (
  SELECT
    COALESCE(m.date, l.date) AS date,
    COALESCE(m.net_sales, 0) AS net_sales,
    COALESCE(m.orders, 0) AS orders,
    COALESCE(m.items_sold, 0) AS items_sold,
    COALESCE(l.hourly_hours, 0) AS hourly_hours,
    COALESCE(l.fulltime_hours, 0) AS fulltime_hours,
    COALESCE(l.hourly_labor_cost, 0) AS hourly_labor_cost,
    COALESCE(l.fulltime_labor_cost, 0) AS fulltime_labor_cost
  FROM `jarvis-bhaga-prod.bhaga.model_labor_daily` m
  FULL OUTER JOIN live_labor l
    ON m.date = l.date
)
SELECT
  DATE_TRUNC(date, WEEK(MONDAY)) AS week_start,
  DATE_ADD(DATE_TRUNC(date, WEEK(MONDAY)), INTERVAL 6 DAY) AS week_end,
  FORMAT_DATE('%G-W%V', DATE_TRUNC(date, WEEK(MONDAY))) AS iso_week,
  SUM(net_sales) AS net_sales,
  SUM(orders) AS orders,
  SUM(items_sold) AS items_sold,
  SUM(hourly_hours) AS hourly_hours,
  SUM(fulltime_hours) AS fulltime_hours,
  SUM(hourly_hours) + SUM(fulltime_hours) AS total_hours,
  SUM(hourly_labor_cost) AS hourly_labor_cost,
  SUM(fulltime_labor_cost) AS fulltime_labor_cost,
  SUM(hourly_labor_cost) + SUM(fulltime_labor_cost) AS total_labor_cost,
  SAFE_DIVIDE(SUM(hourly_labor_cost) + SUM(fulltime_labor_cost), SUM(net_sales)) AS labor_pct,
  SAFE_DIVIDE(SUM(hourly_labor_cost), SUM(net_sales)) AS hourly_pct,
  SAFE_DIVIDE(SUM(fulltime_labor_cost), SUM(net_sales)) AS fulltime_pct
FROM daily
GROUP BY week_start, week_end, iso_week;
