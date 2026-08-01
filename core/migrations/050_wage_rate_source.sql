-- Migration 050: dual-source wage rates (Issue #213).
-- earnings = Earnings & Hours Regular inference
-- pay_info = People → Payroll info → Hourly pay rate (gap-fill for unpaid punchers)
-- roster_stub = roster seed with NULL rate

ALTER TABLE `jarvis-bhaga-prod.bhaga.adp_wage_rates`
  ADD COLUMN IF NOT EXISTS rate_source STRING;
