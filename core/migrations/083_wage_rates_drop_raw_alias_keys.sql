-- 083: drop wage-rate rows keyed by a raw alias spelling — Issue #348
-- 2026-09-29: Wing Huang was scraped as `Huang Wing` before alias onboarding
-- added `Huang Wing -> Huang, Wing`, so every canonical-name join saw no rate
-- (`Huynh Hillary`, 09-03, same race). A raw key with no comma whose alias maps
-- elsewhere is never joined on; the canonical key is scraped nightly and seeds
-- history at 2000-01-01. Prod dry-check 2026-10-02: exactly those two keys,
-- one row each in both tables.
DELETE FROM `jarvis-bhaga-prod.bhaga.adp_wage_rate_history`
WHERE employee_id IN (
  SELECT raw_name FROM `jarvis-bhaga-prod.bhaga.employee_aliases`
  WHERE raw_name != canonical_name AND STRPOS(raw_name, ',') = 0
);
DELETE FROM `jarvis-bhaga-prod.bhaga.adp_wage_rates`
WHERE employee_id IN (
  SELECT raw_name FROM `jarvis-bhaga-prod.bhaga.employee_aliases`
  WHERE raw_name != canonical_name AND STRPOS(raw_name, ',') = 0
);
