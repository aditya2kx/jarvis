-- 026_review_bonus_detail.sql
-- Per-review detail view for the Payroll section (Grafana "6. Payroll").
-- Shows every Google review that actually generated a bonus (total_bonus > 0),
-- with the employees considered, per-employee share, and assignment context.
--
-- Source: google_reviews (BQ-primary, written by process_reviews.py).
-- Pool mode (post 2026-06-08): each eligible member earns equal share of $20;
--   per_employee_bonus = base_credit_each = total_bonus / member_count.
-- Legacy mode: per_employee_bonus = total_bonus / member_count (average).
-- member_count = number of "; "-delimited names in shift_members.
-- All DDL is idempotent (CREATE OR REPLACE VIEW).
--
-- Apply: python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"

-- ─────────────────────────────────────────────────────────────────────────────
-- vw_review_bonus_detail
-- One row per review that contributed to payroll (total_bonus > 0).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_review_bonus_detail` AS
SELECT
  post_ts_ct,
  post_date_ct,
  reviewer,
  rating,
  comment,
  review_url,
  shift_members                                                   AS employees_considered,
  CASE
    WHEN shift_members IS NULL OR TRIM(shift_members) = ''        THEN 0
    ELSE ARRAY_LENGTH(SPLIT(shift_members, '; '))
  END                                                             AS member_count,
  ROUND(
    total_bonus / NULLIF(
      CASE
        WHEN shift_members IS NULL OR TRIM(shift_members) = ''    THEN 0
        ELSE ARRAY_LENGTH(SPLIT(shift_members, '; '))
      END,
      0
    ),
    2
  )                                                               AS per_employee_bonus,
  total_bonus,
  shift_date_credited,
  shift_assignment_reason
FROM `jarvis-bhaga-prod.bhaga.google_reviews`
WHERE total_bonus > 0
ORDER BY post_ts_ct DESC;
