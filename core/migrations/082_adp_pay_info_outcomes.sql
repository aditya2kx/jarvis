-- 082: per-employee pay_info scrape outcome, one row per attempt — Issue #348
-- adp_wage_rates cannot answer "blind for N nights": a failed scrape writes
-- nothing and the earnings load overwrites rate_source/scraped_at_utc. This log
-- feeds the 3-night blind alert. Night = DATE(scraped_at_utc, 'America/Chicago').
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.adp_pay_info_outcomes` (
  employee_id     STRING    NOT NULL,
  scraped_at_utc  TIMESTAMP NOT NULL,
  ok              BOOL      NOT NULL,
  error           STRING
);
