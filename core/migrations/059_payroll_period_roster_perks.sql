-- 059_payroll_period_roster_perks.sql
-- Issue #251: Payroll & People roster = tip-pool PT UNION full-time (Lindsay);
-- expose wage rate, OT, recurring perks (gym $20/biweek). Replaces
-- vw_model_payroll_period from migration 049. Same view name already in
-- GRAFANA_VIEWS — no new Grafana target.
--
-- Apply: BHAGA_DATASTORE=bigquery python3 -c
--   "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.employee_perks` (
  store                    STRING    NOT NULL,
  employee                 STRING    NOT NULL,
  perk_id                  STRING    NOT NULL,
  amount_cents             INT64     NOT NULL,
  cadence                  STRING    NOT NULL,
  adp_earning_description  STRING,
  updated_at               TIMESTAMP,
  updated_by               STRING
);

MERGE `jarvis-bhaga-prod.bhaga.employee_perks` T
USING (
  SELECT
    'palmetto' AS store,
    'Krause, Lindsay' AS employee,
    'gym' AS perk_id,
    2000 AS amount_cents,
    'biweekly' AS cadence,
    'Misc reimbursement' AS adp_earning_description
) S
ON T.store = S.store AND T.employee = S.employee AND T.perk_id = S.perk_id
WHEN NOT MATCHED THEN INSERT (
  store, employee, perk_id, amount_cents, cadence, adp_earning_description,
  updated_at, updated_by
) VALUES (
  S.store, S.employee, S.perk_id, S.amount_cents, S.cadence,
  S.adp_earning_description, CURRENT_TIMESTAMP(), 'migration-059'
)
WHEN MATCHED THEN UPDATE SET
  amount_cents = S.amount_cents,
  cadence = S.cadence,
  adp_earning_description = S.adp_earning_description,
  updated_at = CURRENT_TIMESTAMP(),
  updated_by = 'migration-059';

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
perks AS (
  SELECT
    employee,
    ROUND(SUM(amount_cents) / 100.0, 2) AS perks,
    STRING_AGG(perk_id, '; ' ORDER BY perk_id) AS perk_reason
  FROM `jarvis-bhaga-prod.bhaga.employee_perks`
  GROUP BY employee
),
periods AS (
  SELECT DISTINCT period_start, period_end, is_open
  FROM `jarvis-bhaga-prod.bhaga.model_tip_alloc_period`
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
    ON s.date BETWEEN p.period_start AND p.period_end
   AND IFNULL(s.canonical_name, '') != ''
  GROUP BY p.period_start, p.period_end, p.is_open, s.canonical_name
),
tip_rows AS (
  SELECT
    t.period_start,
    t.period_end,
    t.is_open,
    t.employee,
    t.hours_worked,
    COALESCE(sh.ot_hours, 0) AS ot_hours,
    t.our_calc AS tips_allocated,
    'Part-time' AS labor_type
  FROM `jarvis-bhaga-prod.bhaga.model_tip_alloc_period` t
  LEFT JOIN shift_hours sh
    ON t.period_start = sh.period_start
   AND t.period_end = sh.period_end
   AND t.employee = sh.employee
),
ft_rows AS (
  SELECT
    sh.period_start,
    sh.period_end,
    sh.is_open,
    sh.employee,
    sh.hours_worked,
    sh.ot_hours,
    CAST(0 AS FLOAT64) AS tips_allocated,
    'Full-time' AS labor_type
  FROM shift_hours sh
  LEFT JOIN `jarvis-bhaga-prod.bhaga.adp_wage_rates` w
    ON sh.employee = w.canonical_name
  LEFT JOIN excl e
    ON sh.employee = e.employee
  WHERE (
      e.employee IS NOT NULL
      OR IFNULL(w.is_salaried, FALSE)
      OR IFNULL(w.excluded_from_labor_pct, FALSE)
    )
    AND NOT EXISTS (
      SELECT 1
      FROM `jarvis-bhaga-prod.bhaga.model_tip_alloc_period` t
      WHERE t.period_start = sh.period_start
        AND t.period_end = sh.period_end
        AND t.employee = sh.employee
    )
),
roster AS (
  SELECT * FROM tip_rows
  UNION ALL
  SELECT * FROM ft_rows
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
  ROUND(
    CASE
      WHEN w.wage_rate_dollars IS NULL THEN NULL
      ELSE
        GREATEST(r.hours_worked - COALESCE(r.ot_hours, 0), 0) * w.wage_rate_dollars
        + COALESCE(r.ot_hours, 0) * COALESCE(w.ot_rate_dollars, w.wage_rate_dollars * 1.5)
    END,
  2)                                                                                AS est_gross_pay,
  r.tips_allocated,
  COALESCE(rev.total_bonus, 0)                                                       AS review_bonus,
  COALESCE(rec.recognition_bonus, 0)                                               AS recognition_bonus,
  rec.recognition_reason,
  COALESCE(pk.perks, 0)                                                            AS perks,
  pk.perk_reason,
  ROUND(
    COALESCE(
      CASE
        WHEN w.wage_rate_dollars IS NULL THEN 0
        ELSE
          GREATEST(r.hours_worked - COALESCE(r.ot_hours, 0), 0) * w.wage_rate_dollars
          + COALESCE(r.ot_hours, 0) * COALESCE(w.ot_rate_dollars, w.wage_rate_dollars * 1.5)
      END,
    0)
    + COALESCE(r.tips_allocated, 0)
    + COALESCE(rev.total_bonus, 0)
    + COALESCE(rec.recognition_bonus, 0)
    + COALESCE(pk.perks, 0),
  2)                                                                                AS est_total_pay,
  e.adp_wages_paid,
  e.adp_tips_paid,
  e.adp_bonus_paid,
  e.adp_total_paid,
  ROUND(
    CASE
      WHEN w.wage_rate_dollars IS NULL THEN NULL
      ELSE
        GREATEST(r.hours_worked - COALESCE(r.ot_hours, 0), 0) * w.wage_rate_dollars
        + COALESCE(r.ot_hours, 0) * COALESCE(w.ot_rate_dollars, w.wage_rate_dollars * 1.5)
    END
    - COALESCE(e.adp_wages_paid, 0),
  2)                                                                                AS wage_diff,
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
  ON r.employee = pk.employee
LEFT JOIN `jarvis-bhaga-prod.bhaga.model_tip_alloc_period` t_paid
  ON r.period_start = t_paid.period_start
 AND r.period_end = t_paid.period_end
 AND r.employee = t_paid.employee;
