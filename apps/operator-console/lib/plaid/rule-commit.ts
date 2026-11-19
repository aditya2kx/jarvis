/**
 * Propose-rule commit (Accounting): idempotent, all-or-nothing.
 *
 * The old commit INSERTed the rule, then UPDATEd the matched transactions as a second
 * statement; when the UPDATE lost a serialization race with Reapply the rule row stayed
 * behind, so two failed attempts + one success left three identical ADP WAGE rules
 * (102 → 105). Now one BigQuery transaction does rule + history + backfill, and an
 * identical enabled rule is reused instead of inserted again.
 */
import { digitsMask } from "@/lib/plaid/account-parties";

export interface RuleCriteria {
  match_pattern: string;
  match_operator: string;
  amount_sign: string | null;
  account_mask: string | null;
  from_mask: string | null;
  to_mask: string | null;
  category_id: string;
  subcategory_id: string | null;
}

/** Same criteria + same target ⇒ same rule. */
export function ruleFingerprint(r: RuleCriteria): string {
  return JSON.stringify([
    (r.match_pattern || "").trim(),
    r.match_operator || "contains",
    r.amount_sign || "any",
    digitsMask(r.account_mask),
    digitsMask(r.from_mask),
    digitsMask(r.to_mask),
    r.category_id,
    r.subcategory_id || null,
  ]);
}

/** The enabled rule identical to `draft`, if one exists (first by priority). */
export function findIdenticalRule<T extends RuleCriteria & { id: string; priority: number; enabled: boolean | null }>(
  rules: T[],
  draft: RuleCriteria,
): T | null {
  const fp = ruleFingerprint(draft);
  return (
    [...rules]
      .filter((r) => r.enabled !== false && ruleFingerprint(r) === fp)
      .sort((a, b) => a.priority - b.priority || a.id.localeCompare(b.id))[0] ?? null
  );
}

/** Groups of enabled rules with identical criteria + target (Rules panel "duplicate" badge). */
export function duplicateRuleIds<T extends RuleCriteria & { id: string; priority: number; enabled: boolean | null }>(
  rules: T[],
): Map<string, string> {
  const keeper = new Map<string, T>();
  const dupOf = new Map<string, string>();
  for (const r of [...rules].sort((a, b) => a.priority - b.priority || a.id.localeCompare(b.id))) {
    if (r.enabled === false) continue;
    const fp = ruleFingerprint(r);
    const k = keeper.get(fp);
    if (k) dupOf.set(r.id, k.id);
    else keeper.set(fp, r);
  }
  return dupOf;
}

/**
 * One script: (optionally) insert the rule + a history event, then backfill the
 * selected non-override rows — inside a transaction that rolls back on any error.
 * Params: @create BOOL, @id, @match_operator, @match_pattern, @amount_sign,
 * @account_mask, @from_mask, @to_mask, @category_id, @subcategory_id, @enabled BOOL,
 * @event_id, @by, @ids ARRAY<STRING>.
 */
export function commitRuleScript(t: { rules: string; events: string; txns: string }): string {
  return `
DECLARE pri INT64;
BEGIN
  BEGIN TRANSACTION;
  IF @create THEN
    SET pri = (SELECT IFNULL(MAX(priority), 0) + 10 FROM ${t.rules});
    INSERT INTO ${t.rules} (
      id, priority, match_field, match_operator, match_pattern, amount_sign,
      account_mask, from_mask, to_mask, category_id, subcategory_id,
      confidence, enabled, notes, updated_at
    ) VALUES (
      @id, pri, 'name_or_merchant', @match_operator, @match_pattern, @amount_sign,
      @account_mask, @from_mask, @to_mask, @category_id, @subcategory_id,
      'medium', @enabled, 'Created from Accounting propose-rule', CURRENT_TIMESTAMP()
    );
    INSERT INTO ${t.events} (event_id, rule_id, action, rule_json, note, by_email, at)
    SELECT @event_id, r.id, 'create', TO_JSON_STRING(r), 'propose-rule commit', @by, CURRENT_TIMESTAMP()
    FROM ${t.rules} r WHERE r.id = @id;
  END IF;
  UPDATE ${t.txns}
  SET category_id = @category_id,
      subcategory_id = @subcategory_id,
      rule_id = @id,
      is_internal = IF(@category_id = 'internal_transfers', TRUE, is_internal),
      categorized_at = CURRENT_TIMESTAMP(),
      updated_at = CURRENT_TIMESTAMP()
  WHERE transaction_id IN UNNEST(@ids)
    AND override_category_id IS NULL;
  COMMIT TRANSACTION;
EXCEPTION WHEN ERROR THEN
  ROLLBACK TRANSACTION;
  RAISE USING MESSAGE = @@error.message;
END;`;
}

/** Rule enable/disable (+ history event) as one transaction. */
export function setRuleEnabledScript(t: { rules: string; events: string }): string {
  return `
BEGIN
  BEGIN TRANSACTION;
  UPDATE ${t.rules} SET enabled = @enabled, updated_at = CURRENT_TIMESTAMP() WHERE id = @id;
  INSERT INTO ${t.events} (event_id, rule_id, action, rule_json, note, by_email, at)
  SELECT @event_id, r.id, @action, TO_JSON_STRING(r), @note, @by, CURRENT_TIMESTAMP()
  FROM ${t.rules} r WHERE r.id = @id;
  COMMIT TRANSACTION;
EXCEPTION WHEN ERROR THEN
  ROLLBACK TRANSACTION;
  RAISE USING MESSAGE = @@error.message;
END;`;
}
