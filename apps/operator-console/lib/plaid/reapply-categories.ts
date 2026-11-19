/**
 * Load rules from BQ and reapply categories to transactions (Issue #160).
 * Never overwrites operator overrides. Mirrors skills/plaid_api/categorize.py.
 *
 * Set-based: one read of the rows in scope, rules evaluated in memory, then the rows
 * that changed are written with a few MERGEs from an array-of-STRUCT param. The old
 * one-UPDATE-per-row loop took ~75 min for a full Reapply and held plaid_transactions
 * busy the whole time, so every rule commit in that window hit a serialization error.
 */
import { q, mutate } from "@/lib/bq/client";
import { evaluateRules, type CategoryRule } from "@/lib/plaid/category-rules";
import { plaidFq } from "@/lib/plaid/tables";
import { withSerializeRetry } from "@/lib/plaid/bq-retry";

export interface RuleRow {
  id: string;
  priority: number;
  match_field: string;
  match_operator: string;
  match_pattern: string;
  amount_sign: string | null;
  category_id: string;
  subcategory_id: string | null;
  enabled: boolean | null;
  account_mask: string | null;
  from_mask: string | null;
  to_mask: string | null;
}

export interface CategorizeTxnRow {
  transaction_id: string;
  date: string | null;
  name: string | null;
  merchant_name: string | null;
  amount: number;
  account_mask: string | null;
  counterparty_name: string | null;
  category_id: string | null;
  subcategory_id: string | null;
  rule_id: string | null;
  override_category_id: string | null;
  is_internal: boolean;
}

export interface CategoryUpdate {
  transaction_id: string;
  category_id: string | null;
  subcategory_id: string | null;
  rule_id: string | null;
  is_internal: boolean;
}

export interface CategorizeResult {
  updated: number;
  unchanged: number;
  skipped_override: number;
  scanned: number;
}

/** Rows per MERGE — each chunk is one short statement, so other writers wait seconds, not minutes. */
export const APPLY_CHUNK = 400;

export function toCategoryRule(r: RuleRow, forceEnabled = false): CategoryRule {
  return {
    id: r.id,
    priority: Number(r.priority),
    match_field: (r.match_field as CategoryRule["match_field"]) || "name_or_merchant",
    match_operator: (r.match_operator as CategoryRule["match_operator"]) || "contains",
    match_pattern: r.match_pattern || "",
    amount_sign: (r.amount_sign as CategoryRule["amount_sign"]) || "any",
    category_id: r.category_id,
    subcategory_id: r.subcategory_id,
    enabled: forceEnabled || r.enabled !== false,
    account_mask: r.account_mask,
    from_mask: r.from_mask,
    to_mask: r.to_mask,
  };
}

/** Pure: the rows whose category / rule / internal flag change under `rules`. */
export function planCategoryUpdates(
  txns: CategorizeTxnRow[],
  rules: CategoryRule[],
): { updates: CategoryUpdate[]; unchanged: number; skipped_override: number } {
  const updates: CategoryUpdate[] = [];
  let unchanged = 0;
  let skipped_override = 0;
  for (const t of txns) {
    if (t.override_category_id) {
      skipped_override += 1;
      unchanged += 1;
      continue;
    }
    const match = evaluateRules(
      {
        name: t.name,
        merchant_name: t.merchant_name,
        amount: Number(t.amount),
        account_mask: t.account_mask,
        counterparty_name: t.counterparty_name,
      },
      rules,
    );
    const next: CategoryUpdate = {
      transaction_id: t.transaction_id,
      category_id: match?.category_id ?? null,
      subcategory_id: match?.subcategory_id ?? null,
      rule_id: match?.rule_id ?? null,
      is_internal: match?.category_id === "internal_transfers" ? true : Boolean(t.is_internal),
    };
    if (
      (t.category_id || null) === next.category_id &&
      (t.subcategory_id || null) === next.subcategory_id &&
      (t.rule_id || null) === next.rule_id &&
      Boolean(t.is_internal) === next.is_internal
    ) {
      unchanged += 1;
      continue;
    }
    updates.push(next);
  }
  return { updates, unchanged, skipped_override };
}

export async function loadRules(opts?: { includeDisabled?: boolean }): Promise<RuleRow[]> {
  return q<RuleRow>(
    `SELECT id, priority, match_field, match_operator, match_pattern,
            amount_sign, category_id, subcategory_id, enabled, account_mask,
            from_mask, to_mask
     FROM ${plaidFq("plaid_category_rules")}
     ${opts?.includeDisabled ? "" : "WHERE IFNULL(enabled, TRUE) IS TRUE"}
     ORDER BY priority, id`,
  );
}

export async function loadEnabledRules(): Promise<CategoryRule[]> {
  return (await loadRules()).map((r) => toCategoryRule(r));
}

export type CategorizeScope = {
  itemId?: string;
  transactionIds?: string[];
  /** Rows currently attributed to this rule (revert). */
  ruleId?: string;
  /** Also every row never categorized (no override) — ingest self-heal. */
  includeUncategorized?: boolean;
};

export async function loadTxnsForCategorize(scope?: CategorizeScope): Promise<CategorizeTxnRow[]> {
  let sql = `SELECT t.transaction_id, CAST(t.date AS STRING) AS date, t.name, t.merchant_name,
                    t.amount, a.mask AS account_mask,
                    JSON_VALUE(t.raw_json, '$.counterparties[0].name') AS counterparty_name,
                    t.category_id, t.subcategory_id, t.rule_id, t.override_category_id,
                    IFNULL(t.is_internal, FALSE) AS is_internal
             FROM ${plaidFq("plaid_transactions")} t
             LEFT JOIN \`jarvis-bhaga-prod.bhaga.plaid_accounts\` a
               ON a.account_id = t.account_id
             WHERE 1=1`;
  const params: Record<string, unknown> = {};
  if (scope?.itemId) {
    sql += ` AND t.item_id = @item_id`;
    params.item_id = scope.itemId;
  }
  const any: string[] = [];
  if (scope?.transactionIds?.length) {
    any.push(`t.transaction_id IN UNNEST(@ids)`);
    params.ids = scope.transactionIds;
  }
  if (scope?.ruleId) {
    any.push(`t.rule_id = @rule_id`);
    params.rule_id = scope.ruleId;
  }
  if (scope?.includeUncategorized) {
    any.push(`(t.categorized_at IS NULL AND t.override_category_id IS NULL)`);
  }
  if (any.length) sql += ` AND (${any.join(" OR ")})`;
  sql += ` QUALIFY ROW_NUMBER() OVER (
            PARTITION BY t.transaction_id
            ORDER BY t.updated_at DESC NULLS LAST
          ) = 1`;
  const rows = await q<CategorizeTxnRow>(sql, params);
  return rows.map((r) => ({ ...r, amount: Number(r.amount ?? 0), is_internal: Boolean(r.is_internal) }));
}

const UPDATE_STRUCT = {
  transaction_id: "STRING",
  category_id: "STRING",
  subcategory_id: "STRING",
  rule_id: "STRING",
  is_internal: "BOOL",
};

/** MERGE `updates` in chunks (never touches override rows); `onProgress(done)` after each. */
export async function applyCategoryUpdates(
  updates: CategoryUpdate[],
  onProgress?: (done: number) => Promise<void>,
  chunk = APPLY_CHUNK,
): Promise<void> {
  for (let i = 0; i < updates.length; i += chunk) {
    const rows = updates.slice(i, i + chunk);
    await withSerializeRetry(
      () =>
        mutate(
          `MERGE ${plaidFq("plaid_transactions")} T
           USING (SELECT * FROM UNNEST(@rows)) S
           ON T.transaction_id = S.transaction_id
           WHEN MATCHED AND T.override_category_id IS NULL THEN UPDATE SET
             category_id = S.category_id,
             subcategory_id = S.subcategory_id,
             rule_id = S.rule_id,
             is_internal = S.is_internal,
             categorized_at = CURRENT_TIMESTAMP(),
             updated_at = CURRENT_TIMESTAMP()`,
          { rows },
          { rows: [UPDATE_STRUCT] },
        ),
      { label: `categorize_merge rows=${rows.length} offset=${i}` },
    );
    await onProgress?.(Math.min(i + chunk, updates.length));
  }
}

export async function reapplyPlaidCategories(
  scope?: CategorizeScope,
  hooks?: {
    onPlanned?: (plan: { total: number; unchanged: number; skipped_override: number; scanned: number }) => Promise<void>;
    onProgress?: (done: number) => Promise<void>;
  },
): Promise<CategorizeResult> {
  const [rules, txns] = await Promise.all([loadEnabledRules(), loadTxnsForCategorize(scope)]);
  const plan = planCategoryUpdates(txns, rules);
  await hooks?.onPlanned?.({
    total: plan.updates.length,
    unchanged: plan.unchanged,
    skipped_override: plan.skipped_override,
    scanned: txns.length,
  });
  await applyCategoryUpdates(plan.updates, hooks?.onProgress);
  return {
    updated: plan.updates.length,
    unchanged: plan.unchanged,
    skipped_override: plan.skipped_override,
    scanned: txns.length,
  };
}
