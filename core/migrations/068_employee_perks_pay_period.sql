-- 068_employee_perks_pay_period.sql
-- Issue #267: period-scoped reimbursements (mileage once) alongside recurring
-- gym. pay_period '' = every biweek; 'YYYY-MM-DD..YYYY-MM-DD' = that period only.
-- Replaces vw_model_payroll_period (064 rounding + roster). Same view name.
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c
--   "from core.datastore import ensure_schema; print(ensure_schema())"

ALTER TABLE `jarvis-bhaga-prod.bhaga.employee_perks`
  ADD COLUMN IF NOT EXISTS pay_period STRING;

UPDATE `jarvis-bhaga-prod.bhaga.employee_perks`
SET pay_period = ''
WHERE pay_period IS NULL;

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
shift_hours AS (
  SELECT
    p.period_start,
    p.period_end,
    p.is_open,
    s.canonical_name AS employee,
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
  GROUP BY p.period_start, p.period_end, p.is_open, s.canonical_name
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
    'Part-time' AS labor_type
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
    END AS labor_type
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
    END AS labor_type
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
)
SELECT
  r.period_start,
  r.period_end,
  r.is_open,
  r.employee,
  r.labor_type,
  w.wage_rate_dollars,
  w.ot_rate_dollars,
  r.hours_worked,
  r.ot_hours,
  CAST(ROUND(
    CASE
      WHEN w.wage_rate_dollars IS NULL THEN NULL
      ELSE
        CAST(GREATEST(r.hours_worked - COALESCE(r.ot_hours, 0), 0) AS NUMERIC)
          * CAST(w.wage_rate_dollars AS NUMERIC)
        + CAST(COALESCE(r.ot_hours, 0) AS NUMERIC)
          * COALESCE(
              CAST(w.ot_rate_dollars AS NUMERIC),
              CAST(w.wage_rate_dollars AS NUMERIC) * 1.5
            )
    END,
  2) AS FLOAT64)                                                                    AS est_gross_pay,
  r.tips_allocated,
  COALESCE(rev.total_bonus, 0)                                                       AS review_bonus,
  COALESCE(rec.recognition_bonus, 0)                                               AS recognition_bonus,
  rec.recognition_reason,
  COALESCE(pk.perks, 0)                                                            AS perks,
  pk.perk_reason,
  CAST(ROUND(
    COALESCE(
      CASE
        WHEN w.wage_rate_dollars IS NULL THEN CAST(0 AS NUMERIC)
        ELSE
          CAST(GREATEST(r.hours_worked - COALESCE(r.ot_hours, 0), 0) AS NUMERIC)
            * CAST(w.wage_rate_dollars AS NUMERIC)
          + CAST(COALESCE(r.ot_hours, 0) AS NUMERIC)
            * COALESCE(
                CAST(w.ot_rate_dollars AS NUMERIC),
                CAST(w.wage_rate_dollars AS NUMERIC) * 1.5
              )
      END,
    CAST(0 AS NUMERIC))
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
    CASE
      WHEN w.wage_rate_dollars IS NULL THEN NULL
      ELSE
        CAST(GREATEST(r.hours_worked - COALESCE(r.ot_hours, 0), 0) AS NUMERIC)
          * CAST(w.wage_rate_dollars AS NUMERIC)
        + CAST(COALESCE(r.ot_hours, 0) AS NUMERIC)
          * COALESCE(
              CAST(w.ot_rate_dollars AS NUMERIC),
              CAST(w.wage_rate_dollars AS NUMERIC) * 1.5
            )
    END
    - CAST(COALESCE(e.adp_wages_paid, 0) AS NUMERIC),
  2) AS FLOAT64)                                                                    AS wage_diff,
  ROUND(r.tips_allocated - COALESCE(e.adp_tips_paid, t_paid.adp_paid), 2)          AS tip_diff,
  ROUND(
    COALESCE(rev.total_bonus, 0) + COALESCE(rec.recognition_bonus, 0)
    - COALESCE(e.adp_bonus_paid, 0),
  2)                                                                                AS bonus_diff
FROM roster r
LEFT JOIN `jarvis-bhaga-prod.bhaga.model_review_bonus_period` rev
  ON r.period_start = rev.period_start
 AND r.period_end = rev.period_end
 AND r.employee = rev.employee
LEFT JOIN rec
  ON r.period_start = rec.period_start AND r.employee = rec.employee
LEFT JOIN `jarvis-bhaga-prod.bhaga.adp_wage_rates` w
  ON r.employee = w.canonical_name
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
