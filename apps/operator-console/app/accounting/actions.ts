"use server";

import { createHash } from "crypto";
import { revalidatePath } from "next/cache";
import { operatorEmail, DEFAULT_STORE } from "@/lib/auth/identity";
import { FEATURES } from "@/lib/config/features";
import {
  createLinkToken,
  exchangePublicToken,
  fetchAccounts,
  fetchInstitutionName,
  loadAccessTokenSecret,
  saveAccessTokenSecret,
  transactionsSync,
} from "@/lib/plaid/client";
import {
  deletePlaidTransactions,
  markPlaidTransactionsInternal,
  setPlaidTransactionInternal,
  setPlaidTransactionOverride,
  updatePlaidCursor,
  upsertPlaidAccount,
  upsertPlaidItem,
  upsertPlaidTransaction,
  type PlaidTxnWrite,
} from "@/lib/bq/writes";
import { plaidAccountsForItem, plaidItems, plaidTxnHintsForItem } from "@/lib/bq/queries";
import { suggestInternal } from "@/lib/plaid/internal";
import { reapplyPlaidCategories } from "@/lib/plaid/reapply-categories";
import { mutate } from "@/lib/bq/client";
import { asAck, okAck, failAck, type ActionAck } from "@/lib/actions/types";

/** Production Plaid rejects emails in user.client_user_id (INVALID_FIELD). */
function plaidClientUserId(email: string): string {
  return createHash("sha256").update(`palmetto:${email}`).digest("hex").slice(0, 32);
}

function txnToWrite(txn: Record<string, unknown>, itemId: string): PlaidTxnWrite {
  const pfc = (txn.personal_finance_category || {}) as Record<string, unknown>;
  return {
    transaction_id: String(txn.transaction_id),
    item_id: itemId,
    account_id: txn.account_id != null ? String(txn.account_id) : null,
    date: txn.date != null ? String(txn.date) : null,
    name: txn.name != null ? String(txn.name) : null,
    merchant_name: txn.merchant_name != null ? String(txn.merchant_name) : null,
    amount: typeof txn.amount === "number" ? txn.amount : Number(txn.amount ?? 0),
    iso_currency:
      txn.iso_currency_code != null
        ? String(txn.iso_currency_code)
        : txn.unofficial_currency_code != null
          ? String(txn.unofficial_currency_code)
          : null,
    pending: Boolean(txn.pending),
    pfc_primary: pfc.primary != null ? String(pfc.primary) : null,
    pfc_detailed: pfc.detailed != null ? String(pfc.detailed) : null,
    raw_json: JSON.stringify(txn).slice(0, 10000),
  };
}

async function drainSync(itemId: string, accessToken: string, startCursor: string): Promise<{
  added: number;
  modified: number;
  removed: number;
  cursor: string;
}> {
  let cursor = startCursor;
  let added = 0;
  let modified = 0;
  let removed = 0;
  const touchedIds: string[] = [];
  for (;;) {
    const page = await transactionsSync(accessToken, cursor || null);
    for (const t of page.added) {
      const row = txnToWrite(t, itemId);
      await upsertPlaidTransaction(row);
      touchedIds.push(row.transaction_id);
      added += 1;
    }
    for (const t of page.modified) {
      const row = txnToWrite(t, itemId);
      await upsertPlaidTransaction(row);
      touchedIds.push(row.transaction_id);
      modified += 1;
    }
    const ids = page.removed.map((r) => r.transaction_id).filter((id): id is string => !!id);
    await deletePlaidTransactions(ids);
    removed += ids.length;
    cursor = page.next_cursor;
    if (!page.has_more) break;
  }
  await updatePlaidCursor(DEFAULT_STORE, itemId, cursor);
  try {
    const accounts = await fetchAccounts(accessToken);
    for (const a of accounts) {
      await upsertPlaidAccount({ ...a, item_id: itemId });
    }
  } catch (e) {
    console.error(
      `plaid accounts upsert failed item=${itemId}: ${e instanceof Error ? e.message : String(e)}`,
    );
  }
  // Auto-flag checking↔own-card legs among linked accounts. Never clears an
  // operator un-mark (UPDATE only where is_internal is not already true).
  try {
    const linked = await plaidAccountsForItem(itemId);
    const peers = await plaidTxnHintsForItem(itemId);
    const ids = peers
      .filter((t) => !t.is_internal)
      .filter((t) =>
        suggestInternal(
          {
            transaction_id: t.transaction_id,
            account_id: t.account_id,
            name: t.name,
            merchant_name: t.merchant_name,
            amount: Number(t.amount ?? 0),
            date: t.date,
            pfc_primary: t.pfc_primary,
            pfc_detailed: t.pfc_detailed,
          },
          linked,
          peers.map((p) => ({
            transaction_id: p.transaction_id,
            account_id: p.account_id,
            name: p.name,
            merchant_name: p.merchant_name,
            amount: Number(p.amount ?? 0),
            date: p.date,
            pfc_primary: p.pfc_primary,
            pfc_detailed: p.pfc_detailed,
          })),
        ),
      )
      .map((t) => t.transaction_id);
    const n = await markPlaidTransactionsInternal(ids);
    if (n) console.info(`plaid suggestInternal marked=${n} item=${itemId}`);
  } catch (e) {
    console.error(
      `plaid suggestInternal failed item=${itemId}: ${e instanceof Error ? e.message : String(e)}`,
    );
  }
  // Palmetto taxonomy (#160) — categorize touched rows (never clears overrides).
  try {
    const cat = await reapplyPlaidCategories({
      itemId,
      transactionIds: touchedIds.length ? touchedIds : undefined,
    });
    if (cat.updated) {
      console.info(
        `plaid categorize updated=${cat.updated} unchanged=${cat.unchanged} item=${itemId}`,
      );
    }
  } catch (e) {
    console.error(
      `plaid categorize failed item=${itemId}: ${e instanceof Error ? e.message : String(e)}`,
    );
  }
  return { added, modified, removed, cursor };
}

export async function createPlaidLinkTokenAction(): Promise<ActionAck<string>> {
  return asAck(async () => {
    if (!FEATURES.writePlaidLink) throw new Error("Plaid Link is disabled (FEATURES.writePlaidLink)");
    const email = await operatorEmail();
    const webhook = process.env.PLAID_WEBHOOK_URL?.trim() || undefined;
    // Desktop Link opens Chase OAuth in a popup and returns via postMessage to the
    // opener — do NOT set redirect_uri while the console is behind Cloud Run IAP.
    // A redirect_uri to /accounting/oauth never reaches the app (IAP intercepts),
    // and Plaid surfaces that as Link INTERNAL_SERVER_ERROR / "Something went wrong".
    // Set PLAID_REDIRECT_URI only if the return path is reachable without IAP.
    const redirectUri = process.env.PLAID_REDIRECT_URI?.trim() || undefined;
    return createLinkToken(plaidClientUserId(email), webhook, redirectUri);
  });
}

export async function exchangePlaidPublicTokenAction(publicToken: string): Promise<
  ActionAck<{
    itemId: string;
    sync: { added: number; modified: number; removed: number };
  }>
> {
  return asAck(async () => {
    if (!FEATURES.writePlaidLink) throw new Error("Plaid Link is disabled");
    const by = await operatorEmail();
    const { access_token, item_id } = await exchangePublicToken(publicToken);
    await saveAccessTokenSecret(item_id, access_token);
    const institutionName = await fetchInstitutionName(access_token);
    await upsertPlaidItem(DEFAULT_STORE, item_id, institutionName, by);
    const sync = await drainSync(item_id, access_token, "");
    revalidatePath("/accounting");
    return { itemId: item_id, sync };
  }, "Bank linked.");
}

export async function syncPlaidNowAction(): Promise<
  ActionAck<{
    itemId: string;
    sync: { added: number; modified: number; removed: number };
  }>
> {
  return asAck(async () => {
    if (!FEATURES.writePlaidLink) throw new Error("Plaid sync is disabled");
    await operatorEmail();
    const items = await plaidItems(DEFAULT_STORE);
    const item = items[0];
    if (!item) throw new Error("No linked Plaid Item — Link a bank first");
    const accessToken = await loadAccessTokenSecret(item.item_id);
    const sync = await drainSync(item.item_id, accessToken, item.cursor || "");
    revalidatePath("/accounting");
    return { itemId: item.item_id, sync };
  }, "Sync complete.");
}

export async function setPlaidInternalAction(
  transactionId: string,
  isInternal: boolean,
): Promise<ActionAck> {
  return asAck(async () => {
    if (!FEATURES.writePlaidLink) throw new Error("Plaid writes disabled");
    await operatorEmail();
    await setPlaidTransactionInternal(transactionId, isInternal);
    revalidatePath("/accounting");
  }, "Internal flag updated.");
}

export async function reapplyPlaidCategoriesAction(): Promise<
  ActionAck<{
    updated: number;
    unchanged: number;
    skipped_override: number;
  }>
> {
  return asAck(async () => {
    if (!FEATURES.writePlaidLink) throw new Error("Plaid writes disabled");
    await operatorEmail();
    const result = await reapplyPlaidCategories();
    revalidatePath("/accounting");
    revalidatePath("/home");
    return result;
  }, "Categories reapplied.");
}

export async function setTxnCategoryOverrideAction(
  transactionId: string,
  categoryId: string | null,
  subcategoryId: string | null,
): Promise<ActionAck> {
  return asAck(async () => {
    if (!FEATURES.writePlaidLink) throw new Error("Plaid writes disabled");
    await operatorEmail();
    await setPlaidTransactionOverride(transactionId, categoryId, subcategoryId);
    // If clearing override, restore rule result immediately.
    if (!categoryId) {
      await reapplyPlaidCategories({ transactionIds: [transactionId] });
    } else {
      // Mirror override into display columns for immediate UI consistency.
      await mutate(
        `UPDATE \`jarvis-bhaga-prod.bhaga.plaid_transactions\`
         SET category_id = @category_id,
             subcategory_id = @subcategory_id,
             rule_id = NULL,
             categorized_at = CURRENT_TIMESTAMP(),
             updated_at = CURRENT_TIMESTAMP()
         WHERE transaction_id = @transaction_id`,
        {
          transaction_id: transactionId,
          category_id: categoryId,
          subcategory_id: subcategoryId,
        },
        { category_id: "STRING", subcategory_id: "STRING" },
      );
    }
    revalidatePath("/accounting");
  }, "Category updated.");
}

export async function upsertTaxonomyNodeAction(node: {
  id: string;
  parent_id: string | null;
  slug: string;
  label: string;
  definition?: string | null;
  enabled?: boolean;
  exclude_from_accounting?: boolean | null;
}): Promise<ActionAck> {
  return asAck(async () => {
    if (!FEATURES.writePlaidLink) throw new Error("Plaid writes disabled");
    await operatorEmail();
    await mutate(
      `MERGE \`jarvis-bhaga-prod.bhaga.plaid_taxonomy_nodes\` T
       USING (SELECT @id AS id) S
       ON T.id = S.id
       WHEN MATCHED THEN UPDATE SET
         parent_id = @parent_id, slug = @slug, label = @label,
         definition = @definition, enabled = @enabled,
         exclude_from_accounting = @exclude_from_accounting,
         updated_at = CURRENT_TIMESTAMP()
       WHEN NOT MATCHED THEN INSERT (
         id, parent_id, slug, label, definition, enabled,
         exclude_from_accounting, sort_order, updated_at
       ) VALUES (
         @id, @parent_id, @slug, @label, @definition, @enabled,
         @exclude_from_accounting, 999, CURRENT_TIMESTAMP()
       )`,
      {
        id: node.id,
        parent_id: node.parent_id,
        slug: node.slug,
        label: node.label,
        definition: node.definition ?? null,
        enabled: node.enabled !== false,
        exclude_from_accounting: node.exclude_from_accounting ?? null,
      },
      {
        parent_id: "STRING",
        definition: "STRING",
        enabled: "BOOL",
        exclude_from_accounting: "BOOL",
      },
    );
    revalidatePath("/accounting");
    revalidatePath("/home");
  }, "Taxonomy saved.");
}

export async function setTaxonomyNodeEnabledAction(
  id: string,
  enabled: boolean,
): Promise<ActionAck> {
  return asAck(async () => {
    if (!FEATURES.writePlaidLink) throw new Error("Plaid writes disabled");
    await operatorEmail();
    await mutate(
      `UPDATE \`jarvis-bhaga-prod.bhaga.plaid_taxonomy_nodes\`
       SET enabled = @enabled, updated_at = CURRENT_TIMESTAMP()
       WHERE id = @id`,
      { id, enabled },
      { enabled: "BOOL" },
    );
    revalidatePath("/accounting");
  }, enabled ? "Node enabled." : "Node disabled.");
}

export async function setCategoryRuleEnabledAction(
  id: string,
  enabled: boolean,
): Promise<ActionAck> {
  return asAck(async () => {
    if (!FEATURES.writePlaidLink) throw new Error("Plaid writes disabled");
    await operatorEmail();
    await mutate(
      `UPDATE \`jarvis-bhaga-prod.bhaga.plaid_category_rules\`
       SET enabled = @enabled, updated_at = CURRENT_TIMESTAMP()
       WHERE id = @id`,
      { id, enabled },
      { enabled: "BOOL" },
    );
    revalidatePath("/accounting");
  }, enabled ? "Rule enabled." : "Rule disabled.");
}

export async function dryRunRuleAction(ruleId: string): Promise<ActionAck<number>> {
  return asAck(async () => {
    await operatorEmail();
    const rows = await (
      await import("@/lib/bq/client")
    ).q<{ n: number }>(
      `WITH r AS (
         SELECT match_pattern, match_operator, amount_sign, account_mask
         FROM \`jarvis-bhaga-prod.bhaga.plaid_category_rules\`
         WHERE id = @id
       )
       SELECT COUNT(*) AS n
       FROM \`jarvis-bhaga-prod.bhaga.plaid_transactions\` t
       LEFT JOIN \`jarvis-bhaga-prod.bhaga.plaid_accounts\` a ON a.account_id = t.account_id
       , r
       WHERE STRPOS(LOWER(CONCAT(IFNULL(t.name,''),' ',IFNULL(t.merchant_name,''))),
                    LOWER(r.match_pattern)) > 0
         AND (
           r.amount_sign IS NULL OR r.amount_sign = 'any'
           OR (r.amount_sign = 'positive' AND t.amount > 0)
           OR (r.amount_sign = 'negative' AND t.amount < 0)
         )
         AND (
           r.account_mask IS NULL OR r.account_mask = ''
           OR RIGHT(REGEXP_REPLACE(IFNULL(a.mask,''), r'[^0-9]', ''), 4)
              = RIGHT(REGEXP_REPLACE(r.account_mask, r'[^0-9]', ''), 4)
         )`,
      { id: ruleId },
    );
    return Number(rows[0]?.n ?? 0);
  });
}

export type RuleDraft = {
  match_pattern: string;
  match_operator?: string;
  amount_sign?: string | null;
  account_mask?: string | null;
  category_id: string;
  subcategory_id?: string | null;
};

export type RuleMatchPreview = {
  transaction_id: string;
  date: string;
  name: string | null;
  amount: number;
  has_override: boolean;
};

export async function previewRuleMatchesAction(
  draft: RuleDraft,
): Promise<ActionAck<RuleMatchPreview[]>> {
  return asAck(async () => {
    await operatorEmail();
    const pattern = (draft.match_pattern || "").trim();
    if (!pattern || !draft.category_id) return [];
    const amountSign = draft.amount_sign || "any";
    const accountMask = (draft.account_mask || "").replace(/\D/g, "").slice(-4) || null;
    const rows = await (
      await import("@/lib/bq/client")
    ).q<{
      transaction_id: string;
      date: string;
      name: string | null;
      amount: number;
      has_override: boolean;
    }>(
      `SELECT
         t.transaction_id,
         CAST(t.date AS STRING) AS date,
         t.name,
         t.amount,
         t.override_category_id IS NOT NULL AS has_override
       FROM \`jarvis-bhaga-prod.bhaga.plaid_transactions\` t
       LEFT JOIN \`jarvis-bhaga-prod.bhaga.plaid_accounts\` a ON a.account_id = t.account_id
       WHERE STRPOS(LOWER(CONCAT(IFNULL(t.name,''),' ',IFNULL(t.merchant_name,''))),
                    LOWER(@pattern)) > 0
         AND (
           @amount_sign = 'any'
           OR (@amount_sign = 'positive' AND t.amount > 0)
           OR (@amount_sign = 'negative' AND t.amount < 0)
         )
         AND (
           @account_mask = ''
           OR RIGHT(REGEXP_REPLACE(IFNULL(a.mask,''), r'[^0-9]', ''), 4) = @account_mask
         )
       ORDER BY t.date DESC
       LIMIT 500`,
      {
        pattern,
        amount_sign: amountSign,
        account_mask: accountMask ?? "",
      },
    );
    return rows.map((r) => ({
      transaction_id: r.transaction_id,
      date: r.date,
      name: r.name,
      amount: Number(r.amount),
      has_override: Boolean(r.has_override),
    }));
  });
}

export async function commitRuleFromTxnAction(input: {
  draft: RuleDraft;
  selectedTxnIds: string[];
  applyFuture: boolean;
}): Promise<ActionAck<{ ruleId: string; applied: number; skipped_override: number }>> {
  try {
    if (!FEATURES.writePlaidLink) throw new Error("Plaid writes disabled");
    await operatorEmail();
    const pattern = (input.draft.match_pattern || "").trim();
    if (!pattern || !input.draft.category_id) {
      throw new Error("pattern and category_id required");
    }
    const slug = pattern
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_|_$/g, "")
      .slice(0, 40);
    const ruleId = `op_${slug || "rule"}_${Date.now().toString(36)}`;
    const accountMask =
      (input.draft.account_mask || "").replace(/\D/g, "").slice(-4) || null;
    const amountSign = input.draft.amount_sign || "any";

    // Priority: after seeded rules — use max+10
    const priRows = await (
      await import("@/lib/bq/client")
    ).q<{ p: number }>(
      `SELECT IFNULL(MAX(priority), 0) + 10 AS p
       FROM \`jarvis-bhaga-prod.bhaga.plaid_category_rules\``,
    );
    const priority = Number(priRows[0]?.p ?? 500);

    await mutate(
      `INSERT INTO \`jarvis-bhaga-prod.bhaga.plaid_category_rules\` (
         id, priority, match_field, match_operator, match_pattern, amount_sign,
         account_mask, category_id, subcategory_id, confidence, enabled, notes, updated_at
       ) VALUES (
         @id, @priority, 'name_or_merchant', @match_operator, @match_pattern, @amount_sign,
         @account_mask, @category_id, @subcategory_id, 'medium', @enabled,
         'Created from Accounting propose-rule', CURRENT_TIMESTAMP()
       )`,
      {
        id: ruleId,
        priority,
        match_operator: input.draft.match_operator || "contains",
        match_pattern: pattern,
        amount_sign: amountSign,
        account_mask: accountMask,
        category_id: input.draft.category_id,
        subcategory_id: input.draft.subcategory_id ?? null,
        enabled: input.applyFuture !== false,
      },
      {
        account_mask: "STRING",
        subcategory_id: "STRING",
        enabled: "BOOL",
      },
    );

    let applied = 0;
    let skipped_override = 0;
    for (const txnId of input.selectedTxnIds) {
      const check = await (
        await import("@/lib/bq/client")
      ).q<{ ov: string | null }>(
        `SELECT override_category_id AS ov
         FROM \`jarvis-bhaga-prod.bhaga.plaid_transactions\`
         WHERE transaction_id = @id`,
        { id: txnId },
      );
      if (check[0]?.ov) {
        skipped_override += 1;
        continue;
      }
      await mutate(
        `UPDATE \`jarvis-bhaga-prod.bhaga.plaid_transactions\`
         SET category_id = @category_id,
             subcategory_id = @subcategory_id,
             rule_id = @rule_id,
             is_internal = IF(@category_id = 'internal_transfers', TRUE, is_internal),
             categorized_at = CURRENT_TIMESTAMP(),
             updated_at = CURRENT_TIMESTAMP()
         WHERE transaction_id = @transaction_id
           AND override_category_id IS NULL`,
        {
          transaction_id: txnId,
          category_id: input.draft.category_id,
          subcategory_id: input.draft.subcategory_id ?? null,
          rule_id: ruleId,
        },
        {
          category_id: "STRING",
          subcategory_id: "STRING",
          rule_id: "STRING",
        },
      );
      applied += 1;
    }

    revalidatePath("/accounting");
    revalidatePath("/home");
    return okAck({
      data: { ruleId, applied, skipped_override },
      message: `Rule committed — applied ${applied}.`,
    });
  } catch (e) {
    return failAck(e);
  }
}

/** Disable a rule and reapply categories (evidence revert / undo). */
export async function revertRuleEvidenceAction(
  ruleId: string,
): Promise<
  ActionAck<{
    disabled: boolean;
    reapply: { updated: number; unchanged: number; skipped_override: number };
  }>
> {
  return asAck(async () => {
    if (!FEATURES.writePlaidLink) throw new Error("Plaid writes disabled");
    await operatorEmail();
    await mutate(
      `UPDATE \`jarvis-bhaga-prod.bhaga.plaid_category_rules\`
       SET enabled = FALSE, updated_at = CURRENT_TIMESTAMP()
       WHERE id = @id`,
      { id: ruleId },
    );
    // Clear rule_id on txns that still point at this rule, then reapply.
    await mutate(
      `UPDATE \`jarvis-bhaga-prod.bhaga.plaid_transactions\`
       SET rule_id = NULL, updated_at = CURRENT_TIMESTAMP()
       WHERE rule_id = @id AND override_category_id IS NULL`,
      { id: ruleId },
    );
    const reapply = await reapplyPlaidCategories();
    revalidatePath("/accounting");
    revalidatePath("/home");
    return { disabled: true, reapply };
  }, "Rule reverted.");
}

export async function setTaxonomyExcludeAction(
  id: string,
  excludeFromAccounting: boolean | null,
): Promise<ActionAck> {
  return asAck(async () => {
    if (!FEATURES.writePlaidLink) throw new Error("Plaid writes disabled");
    await operatorEmail();
    await mutate(
      `UPDATE \`jarvis-bhaga-prod.bhaga.plaid_taxonomy_nodes\`
       SET exclude_from_accounting = @exclude_from_accounting,
           updated_at = CURRENT_TIMESTAMP()
       WHERE id = @id`,
      { id, exclude_from_accounting: excludeFromAccounting },
      { exclude_from_accounting: "BOOL" },
    );
    revalidatePath("/accounting");
    revalidatePath("/home");
  }, "Exclude flag updated.");
}
