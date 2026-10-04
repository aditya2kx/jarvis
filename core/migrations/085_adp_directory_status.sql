-- 085: ADP People Directory snapshot (every status), appended by the nightly
-- pay_info pass. The shift-draft roster reads the latest snapshot: Active staff
-- are draftable, Terminated / Leave of absence never are. All rows of one
-- snapshot share scraped_at_utc.
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.adp_directory_status` (
  store              STRING    NOT NULL,
  employee_name      STRING    NOT NULL,
  employment_status  STRING,
  scraped_at_utc     TIMESTAMP NOT NULL
);
