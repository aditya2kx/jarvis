-- 049_payroll_recognition_in_period.sql
-- Issue #206: fold operator recognition_bonuses into vw_model_payroll_period
-- alongside model_review_bonus_period so est_total_pay and bonus_diff include
-- recognition (wage_diff stays wages-only). Console Payroll & People reads this
-- view; no new Grafana target (same view name already in GRAFANA_VIEWS).
--
-- recognition_bonuses.pay_period is 'YYYY-MM-DD..YYYY-MM-DD' (migration 033).
-- Aggregate SUM(amount_cents)/100 + STRING_AGG(reason) per period+employee.

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
    SAFE.PARSE_DATE('%Y-%m-%d', SPLIT(pay_period, '..')[SAFE_OFFSET(0)]) AS period_start,
    SAFE.PARSE_DATE('%Y-%m-%d', SPLIT(pay_period, '..')[SAFE_OFFSET(1)]) AS period_end,
    employee,
    ROUND(SUM(amount_cents) / 100.0, 2) AS recognition_bonus,
    STRING_AGG(
      NULLIF(TRIM(reason), ''),
      '; '
      ORDER BY updated_at
    ) AS recognition_reason
  FROM `jarvis-bhaga-prod.bhaga.recognition_bonuses`
  WHERE SAFE.PARSE_DATE('%Y-%m-%d', SPLIT(pay_period, '..')[SAFE_OFFSET(0)]) IS NOT NULL
    AND SAFE.PARSE_DATE('%Y-%m-%d', SPLIT(pay_period, '..')[SAFE_OFFSET(1)]) IS NOT NULL
  GROUP BY period_start, period_end, employee
)
SELECT
  t.period_start,
  t.period_end,
  t.is_open,
  t.employee,
  t.hours_worked,
  ROUND(t.hours_worked * w.wage_rate_dollars, 2)                                  AS est_gross_pay,
  t.our_calc                                                                       AS tips_allocated,
  COALESCE(r.total_bonus, 0)                                                       AS review_bonus,
  COALESCE(rec.recognition_bonus, 0)                                               AS recognition_bonus,
  rec.recognition_reason,
  ROUND(
    COALESCE(t.hours_worked * w.wage_rate_dollars, 0)
    + COALESCE(t.our_calc, 0)
    + COALESCE(r.total_bonus, 0)
    + COALESCE(rec.recognition_bonus, 0),
  2)                                                                                AS est_total_pay,
  e.adp_wages_paid,
  e.adp_tips_paid,
  e.adp_bonus_paid,
  e.adp_total_paid,
  ROUND(t.hours_worked * w.wage_rate_dollars - COALESCE(e.adp_wages_paid, 0), 2) AS wage_diff,
  ROUND(t.our_calc - COALESCE(e.adp_tips_paid, t.adp_paid), 2)                   AS tip_diff,
  ROUND(
    COALESCE(r.total_bonus, 0) + COALESCE(rec.recognition_bonus, 0)
    - COALESCE(e.adp_bonus_paid, 0),
  2)                                                                                AS bonus_diff
FROM `jarvis-bhaga-prod.bhaga.model_tip_alloc_period` t
LEFT JOIN `jarvis-bhaga-prod.bhaga.model_review_bonus_period` r
  USING (period_start, period_end, employee)
LEFT JOIN rec
  USING (period_start, period_end, employee)
LEFT JOIN `jarvis-bhaga-prod.bhaga.adp_wage_rates` w
  ON t.employee = w.canonical_name
LEFT JOIN earn e
  ON t.period_start = e.period_start
  AND t.period_end  = e.period_end
  AND t.employee    = e.employee;
