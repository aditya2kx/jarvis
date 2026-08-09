-- 057_plaid_rule_from_to_masks.sql
-- Directional from/to account masks on category rules (optional; AND with name pattern).
-- Legacy account_mask = linked-account filter (kept; do NOT backfill into from_mask —
-- linked mask ≠ directional from on inflows).

ALTER TABLE `jarvis-bhaga-prod.bhaga.plaid_category_rules`
ADD COLUMN IF NOT EXISTS from_mask STRING;

ALTER TABLE `jarvis-bhaga-prod.bhaga.plaid_category_rules`
ADD COLUMN IF NOT EXISTS to_mask STRING;
