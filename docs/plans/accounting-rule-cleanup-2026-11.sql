-- Accounting rule cleanup — Nov 2026 (duplicate rules from failed propose-rule commits).
--
-- NOT RUN BY ANY CODE PATH. Operator-reviewed, reversible, run by hand from an ADC surface
-- (docs/contributing/gcp-access.md) only after the console fix PR is deployed (migration 098
-- creates plaid_category_rule_events) and the operator approves on the PR.
--
-- Context: two propose-rule commits failed on 2026-11-18 ~22:21–22:22 UTC with
-- "Could not serialize access … due to concurrent update" while a Reapply ran. The old
-- commit INSERTed the rule before the transaction UPDATE, so each failure left a rule row;
-- the third (successful) commit added a third identical rule. Rule count 102 → 105.
--
-- Preferred path needs no SQL: on the fixed console, Accounting → Rules (search
-- "adp_wage") badges identical rules "Duplicate of …" (the badge names the
-- highest-priority copy). Disable the two orphans listed in Step 2 and keep
-- op_adp_wage_mwmr0pg2 (the CFO's successful commit, holds the 25 rows). Use this file
-- only if the operator wants it done in one reviewed batch.

-- ── Step 0 — dry run (read-only): every enabled rule identical to a higher-priority one. ──
SELECT d.id AS duplicate_rule, d.dup_of, d.priority, CAST(d.updated_at AS STRING) AS created_at,
       (SELECT COUNT(*) FROM `jarvis-bhaga-prod.bhaga.plaid_transactions` t WHERE t.rule_id = d.id) AS txns_on_rule
FROM (
  SELECT a.id, a.priority, a.updated_at, MIN(b.id) AS dup_of
  FROM `jarvis-bhaga-prod.bhaga.plaid_category_rules` a
  JOIN `jarvis-bhaga-prod.bhaga.plaid_category_rules` b
    ON a.id != b.id
   AND (b.priority < a.priority OR (b.priority = a.priority AND b.id < a.id))
   AND IFNULL(a.match_pattern, '') = IFNULL(b.match_pattern, '')
   AND IFNULL(a.match_operator, '') = IFNULL(b.match_operator, '')
   AND IFNULL(a.amount_sign, 'any') = IFNULL(b.amount_sign, 'any')
   AND IFNULL(a.account_mask, '') = IFNULL(b.account_mask, '')
   AND IFNULL(a.from_mask, '') = IFNULL(b.from_mask, '')
   AND IFNULL(a.to_mask, '') = IFNULL(b.to_mask, '')
   AND a.category_id = b.category_id
   AND IFNULL(a.subcategory_id, '') = IFNULL(b.subcategory_id, '')
  WHERE IFNULL(a.enabled, TRUE) AND IFNULL(b.enabled, TRUE)
  GROUP BY a.id, a.priority, a.updated_at
) d
ORDER BY d.priority;
-- 2026-11-19 result: op_adp_wage_mwmosx92 (dup of op_adp_wage_mwmosidb, 0 txns) and
-- op_adp_wage_mwmr0pg2 (dup of op_adp_wage_mwmosidb, 25 txns). Keep the one the CFO
-- committed successfully (op_adp_wage_mwmr0pg2, holds the 25 rows); disable the two
-- orphans from the failed attempts.

-- ── Step 1 — backup (reversible point). ──
CREATE TABLE `jarvis-bhaga-prod.bhaga.plaid_category_rules_bak_20261119`
COPY `jarvis-bhaga-prod.bhaga.plaid_category_rules`;
ALTER TABLE `jarvis-bhaga-prod.bhaga.plaid_category_rules_bak_20261119`
SET OPTIONS (expiration_timestamp = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 30 DAY));

-- ── Step 2 — disable (never delete) the two orphans, with history. Expect 2 rows. ──
BEGIN
  BEGIN TRANSACTION;
  UPDATE `jarvis-bhaga-prod.bhaga.plaid_category_rules`
  SET enabled = FALSE,
      notes = CONCAT(IFNULL(notes, ''), ' | Disabled 2026-11: orphan of a failed commit (duplicate of op_adp_wage_mwmr0pg2)'),
      updated_at = CURRENT_TIMESTAMP()
  WHERE id IN ('op_adp_wage_mwmosidb', 'op_adp_wage_mwmosx92') AND IFNULL(enabled, TRUE);
  INSERT INTO `jarvis-bhaga-prod.bhaga.plaid_category_rule_events`
    (event_id, rule_id, action, rule_json, note, by_email, event_at)
  SELECT GENERATE_UUID(), r.id, 'disable', TO_JSON_STRING(r), 'duplicate cleanup 2026-11 (reviewed on PR)', SESSION_USER(), CURRENT_TIMESTAMP()
  FROM `jarvis-bhaga-prod.bhaga.plaid_category_rules` r
  WHERE r.id IN ('op_adp_wage_mwmosidb', 'op_adp_wage_mwmosx92');
  COMMIT TRANSACTION;
EXCEPTION WHEN ERROR THEN
  ROLLBACK TRANSACTION;
  RAISE USING MESSAGE = @@error.message;
END;

-- Verify: Step 0 returns no rows; rule count unchanged (disabled, not deleted).
-- Rollback: re-enable from the backup —
--   UPDATE `jarvis-bhaga-prod.bhaga.plaid_category_rules` r SET enabled = b.enabled, notes = b.notes
--   FROM `jarvis-bhaga-prod.bhaga.plaid_category_rules_bak_20261119` b
--   WHERE r.id = b.id AND r.id IN ('op_adp_wage_mwmosidb', 'op_adp_wage_mwmosx92');

-- ── Not SQL: wrong auto-picked subcategories (bug #5) ──
-- The 2026-11-18 single-row categorizations and op_ rules committed from that dialog
-- inherited the parent's first subcategory (e.g. Frozen freight, ADP wage pay, Paid
-- digital ads, Amazon refund). Some are genuinely right (ADP WAGE → ADP wage pay), so
-- they need the CFO's judgement row by row, not a blanket UPDATE. Read-only list:
--
-- WITH fc AS (
--   SELECT parent_id, id AS first_child FROM (
--     SELECT id, parent_id, ROW_NUMBER() OVER (PARTITION BY parent_id ORDER BY sort_order, label) rn
--     FROM `jarvis-bhaga-prod.bhaga.plaid_taxonomy_nodes`
--     WHERE IFNULL(enabled, TRUE) AND parent_id IS NOT NULL) WHERE rn = 1)
-- SELECT 'override' AS kind, t.transaction_id AS id, CAST(t.date AS STRING) AS d,
--        IFNULL(t.merchant_name, t.name) AS what, t.amount, t.override_subcategory_id AS sub
-- FROM `jarvis-bhaga-prod.bhaga.plaid_transactions` t
-- JOIN fc ON fc.parent_id = t.override_category_id AND fc.first_child = t.override_subcategory_id
-- UNION ALL
-- SELECT 'rule', r.id, NULL, r.match_pattern, NULL, r.subcategory_id
-- FROM `jarvis-bhaga-prod.bhaga.plaid_category_rules` r
-- JOIN fc ON fc.parent_id = r.category_id AND fc.first_child = r.subcategory_id
-- WHERE STARTS_WITH(r.id, 'op_');
--
-- Fix through the console (no hand SQL): overrides — open the row, pick the right
-- Subcategory (or "None — category only"); rules — Rules → Revert, then re-propose with
-- the right target.
