/**
 * Load rules from BQ and reapply categories to transactions (Issue #160).
 * Never overwrites operator overrides.
 */
import { q, mutate } from "@/lib/bq/client";
import {
  evaluateRules,
  type CategoryRule,
} from "@/lib/plaid/category-rules";

interface RuleRow {
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

interface TxnRow {
  transaction_id: string;
  name: string | null;
  merchant_name: string | null;
  amount: number;
  account_mask: string | null;
  counterparty_name: string | null;
  category_id: string | null;
  subcategory_id: string | null;
  rule_id: string | null;
  override_category_id: string | null;
}

export async function reapplyPlaidCategories(opts?: {
  itemId?: string;
  transactionIds?: string[];
}): Promise<{ updated: number; unchanged: number; skipped_override: number }> {
  const ruleRows = await q<RuleRow>(
    `SELECT id, priority, match_field, match_operator, match_pattern,
            amount_sign, category_id, subcategory_id, enabled, account_mask,
            from_mask, to_mask
     FROM \`jarvis-bhaga-prod.bhaga.plaid_category_rules\`
     WHERE IFNULL(enabled, TRUE) IS TRUE
     ORDER BY priority, id`,
  );
  const rules: CategoryRule[] = ruleRows.map((r) => ({
    id: r.id,
    priority: Number(r.priority),
    match_field: (r.match_field as CategoryRule["match_field"]) || "name_or_merchant",
    match_operator: (r.match_operator as CategoryRule["match_operator"]) || "contains",
    match_pattern: r.match_pattern || "",
    amount_sign: (r.amount_sign as CategoryRule["amount_sign"]) || "any",
    category_id: r.category_id,
    subcategory_id: r.subcategory_id,
    enabled: r.enabled !== false,
    account_mask: r.account_mask,
    from_mask: r.from_mask,
    to_mask: r.to_mask,
  }));

  let sql = `SELECT t.transaction_id, t.name, t.merchant_name, t.amount,
                    a.mask AS account_mask,
                    JSON_VALUE(t.raw_json, '$.counterparties[0].name') AS counterparty_name,
                    t.category_id, t.subcategory_id, t.rule_id, t.override_category_id,
                    IFNULL(t.is_internal, FALSE) AS is_internal
             FROM \`jarvis-bhaga-prod.bhaga.plaid_transactions\` t
             LEFT JOIN \`jarvis-bhaga-prod.bhaga.plaid_accounts\` a
               ON a.account_id = t.account_id
             WHERE 1=1`;
  const params: Record<string, unknown> = {};
  if (opts?.itemId) {
    sql += ` AND t.item_id = @item_id`;
    params.item_id = opts.itemId;
  }
  if (opts?.transactionIds?.length) {
    sql += ` AND t.transaction_id IN UNNEST(@ids)`;
    params.ids = opts.transactionIds;
  }
  sql += ` QUALIFY ROW_NUMBER() OVER (
            PARTITION BY t.transaction_id
            ORDER BY t.updated_at DESC NULLS LAST
          ) = 1`;

  const txns = await q<TxnRow & { is_internal: boolean }>(sql, params);
  let updated = 0;
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
        amount: t.amount,
        account_mask: t.account_mask,
        counterparty_name: t.counterparty_name,
      },
      rules,
    );
    const newCat = match?.category_id ?? null;
    const newSub = match?.subcategory_id ?? null;
    const newRule = match?.rule_id ?? null;
    const newInternal = newCat === "internal_transfers" ? true : Boolean(t.is_internal);
    if (
      (t.category_id || null) === newCat &&
      (t.subcategory_id || null) === newSub &&
      (t.rule_id || null) === newRule &&
      Boolean(t.is_internal) === newInternal
    ) {
      unchanged += 1;
      continue;
    }
    await mutate(
      `UPDATE \`jarvis-bhaga-prod.bhaga.plaid_transactions\`
       SET category_id = @category_id,
           subcategory_id = @subcategory_id,
           rule_id = @rule_id,
           is_internal = @is_internal,
           categorized_at = CURRENT_TIMESTAMP(),
           updated_at = CURRENT_TIMESTAMP()
       WHERE transaction_id = @transaction_id
         AND override_category_id IS NULL`,
      {
        transaction_id: t.transaction_id,
        category_id: newCat,
        subcategory_id: newSub,
        rule_id: newRule,
        is_internal: newInternal,
      },
      {
        category_id: "STRING",
        subcategory_id: "STRING",
        rule_id: "STRING",
        is_internal: "BOOL",
      },
    );
    updated += 1;
  }

  return { updated, unchanged, skipped_override };
}
