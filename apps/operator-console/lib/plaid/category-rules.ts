/**
 * Pure Copilot-style category rule evaluation (Issue #160).
 * Mirrored by skills/plaid_api/category_rules.py — keep semantics identical.
 */

export type MatchOperator =
  | "contains"
  | "contains_any"
  | "equals_or_contains"
  | "regex";

export type AmountSign = "positive" | "negative" | "any";

export interface CategoryRule {
  id: string;
  priority: number;
  match_field: "name" | "merchant_name" | "name_or_merchant";
  match_operator: MatchOperator;
  match_pattern: string;
  amount_sign: AmountSign | null;
  category_id: string;
  subcategory_id: string | null;
  enabled: boolean;
}

export interface TxnForRules {
  name: string | null;
  merchant_name: string | null;
  amount: number | null;
  override_category_id?: string | null;
  override_subcategory_id?: string | null;
}

export interface RuleMatch {
  rule_id: string;
  category_id: string;
  subcategory_id: string | null;
}

function haystack(
  txn: TxnForRules,
  field: CategoryRule["match_field"] | "name",
): string {
  const name = txn.name || "";
  const merchant = txn.merchant_name || "";
  if (field === "merchant_name") return merchant;
  if (field === "name") return `${name} ${merchant}`.trim(); // seed "name" → both
  return `${name} ${merchant}`.trim();
}

function amountOk(amount: number | null | undefined, sign: AmountSign | null): boolean {
  if (!sign || sign === "any") return true;
  const a = Number(amount ?? 0);
  if (sign === "positive") return a > 0;
  if (sign === "negative") return a < 0;
  return true;
}

function fieldMatches(text: string, operator: MatchOperator, pattern: string): boolean {
  if (!pattern) return false;
  const hay = text.toLowerCase();
  const op = operator || "contains";
  if (op === "contains") return hay.includes(pattern.toLowerCase());
  if (op === "contains_any") {
    return pattern
      .split("|")
      .map((p) => p.trim())
      .filter(Boolean)
      .some((p) => hay.includes(p.toLowerCase()));
  }
  if (op === "equals_or_contains") {
    const p = pattern.toLowerCase().trim();
    return hay.trim() === p || hay.includes(p);
  }
  if (op === "regex") {
    try {
      return new RegExp(pattern, "i").test(text);
    } catch {
      console.info(`plaid categorize skip invalid regex pattern=${pattern}`);
      return false;
    }
  }
  return false;
}

export function ruleMatches(txn: TxnForRules, rule: CategoryRule): boolean {
  if (!rule.enabled) return false;
  if (!amountOk(txn.amount, rule.amount_sign)) return false;
  const text = haystack(txn, rule.match_field);
  return fieldMatches(text, rule.match_operator, rule.match_pattern);
}

/** First enabled rule by ascending priority; null if none. */
export function evaluateRules(
  txn: TxnForRules,
  rules: CategoryRule[],
): RuleMatch | null {
  const ordered = [...rules]
    .filter((r) => r.enabled)
    .sort((a, b) => a.priority - b.priority || a.id.localeCompare(b.id));
  for (const rule of ordered) {
    try {
      if (ruleMatches(txn, rule)) {
        return {
          rule_id: rule.id,
          category_id: rule.category_id,
          subcategory_id: rule.subcategory_id,
        };
      }
    } catch (e) {
      console.info(
        `plaid categorize skip rule_id=${rule.id}: ${e instanceof Error ? e.message : String(e)}`,
      );
    }
  }
  return null;
}

export function effectiveCategory(
  txn: TxnForRules,
  match: RuleMatch | null,
): {
  category_id: string | null;
  subcategory_id: string | null;
  rule_id: string | null;
  source: "override" | "rule" | "none";
} {
  if (txn.override_category_id) {
    return {
      category_id: txn.override_category_id,
      subcategory_id: txn.override_subcategory_id ?? null,
      rule_id: null,
      source: "override",
    };
  }
  if (match) {
    return {
      category_id: match.category_id,
      subcategory_id: match.subcategory_id,
      rule_id: match.rule_id,
      source: "rule",
    };
  }
  return {
    category_id: null,
    subcategory_id: null,
    rule_id: null,
    source: "none",
  };
}
