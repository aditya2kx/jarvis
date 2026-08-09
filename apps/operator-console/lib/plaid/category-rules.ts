/**
 * Pure Copilot-style category rule evaluation (Issue #160).
 * Mirrored by skills/plaid_api/category_rules.py — keep semantics identical.
 */

import { digitsMask, resolveFromTo } from "@/lib/plaid/account-parties";

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
  /** Optional when from_mask and/or to_mask is set. */
  match_pattern: string;
  amount_sign: AmountSign | null;
  category_id: string;
  subcategory_id: string | null;
  enabled: boolean;
  /** Legacy: constrain linked Plaid account last-4. */
  account_mask?: string | null;
  /** Optional: constrain resolved from-side last-4. */
  from_mask?: string | null;
  /** Optional: constrain resolved to-side last-4. */
  to_mask?: string | null;
}

export interface TxnForRules {
  name: string | null;
  merchant_name: string | null;
  amount: number | null;
  /** Linked Plaid account last-4. */
  account_mask?: string | null;
  counterparty_name?: string | null;
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
  if (field === "name") return `${name} ${merchant}`.trim();
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

function maskEquals(
  got: string | null | undefined,
  wantRaw: string | null | undefined,
): boolean {
  const want = digitsMask(wantRaw);
  if (!want) return true;
  const got4 = digitsMask(got);
  return got4.length === 4 && got4 === want;
}

/** True when the rule has at least one of pattern / from / to / legacy account_mask. */
export function ruleHasMatchCriteria(rule: CategoryRule): boolean {
  return Boolean(
    (rule.match_pattern || "").trim() ||
      digitsMask(rule.from_mask) ||
      digitsMask(rule.to_mask) ||
      digitsMask(rule.account_mask),
  );
}

export function ruleMatches(txn: TxnForRules, rule: CategoryRule): boolean {
  if (!rule.enabled) return false;
  if (!ruleHasMatchCriteria(rule)) return false;
  if (!amountOk(txn.amount, rule.amount_sign)) return false;

  // Legacy linked-account filter (Issue #189).
  if (!maskEquals(txn.account_mask, rule.account_mask)) return false;

  const parties = resolveFromTo({
    amount: Number(txn.amount ?? 0),
    our_mask: txn.account_mask,
    name: txn.name,
    merchant_name: txn.merchant_name,
    counterparty_name: txn.counterparty_name,
  });
  if (!maskEquals(parties.from.mask, rule.from_mask)) return false;
  if (!maskEquals(parties.to.mask, rule.to_mask)) return false;

  const pattern = (rule.match_pattern || "").trim();
  if (pattern) {
    const text = haystack(txn, rule.match_field);
    if (!fieldMatches(text, rule.match_operator, pattern)) return false;
  }
  return true;
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
