-- 040: ADP Payroll Liability employer-tax snapshot (burden calibration)

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.adp_payroll_liability` (
  check_date              DATE NOT NULL,
  payroll_label           STRING NOT NULL,
  er_social_security      FLOAT64,
  er_medicare             FLOAT64,
  er_futa                 FLOAT64,
  er_sui                  FLOAT64,
  er_tax_total            FLOAT64,
  pay_by_pay              FLOAT64,
  ee_tax_total            FLOAT64,
  approx_ss_wage_base     FLOAT64,
  effective_burden_pct    FLOAT64,
  scraped_at_utc          TIMESTAMP,
  materialized_at_utc     TIMESTAMP
);
