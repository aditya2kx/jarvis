-- 085: ADP employment status (Active / Terminated / Leave of absence) per employee,
-- read off the People Directory row the nightly pay_info scrape opens. The shift
-- draft treats everyone not Terminated as available. NULL = not read yet.
ALTER TABLE `jarvis-bhaga-prod.bhaga.adp_wage_rates`
  ADD COLUMN IF NOT EXISTS employment_status STRING;
