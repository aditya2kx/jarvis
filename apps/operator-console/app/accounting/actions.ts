"use server";

import { createHash, randomUUID } from "crypto";
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
  dedupePlaidTransactions,
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
import {
  loadRules,
  loadTxnsForCategorize,
  reapplyPlaidCategories,
  toCategoryRule,
} from "@/lib/plaid/reapply-categories";
import { mutate, q } from "@/lib/bq/client";
import { plaidFq } from "@/lib/plaid/tables";
import { withSerializeRetry } from "@/lib/plaid/bq-retry";
import { busyMessage, runCategorizeJob } from "@/lib/plaid/categorize-jobs";
import type { CategorizeJob } from "@/lib/plaid/categorize-job-status";
import { evaluateRules, ruleMatches } from "@/lib/plaid/category-rules";
import { commitRuleScript, findIdenticalRule, setRuleEnabledScript } from "@/lib/plaid/rule-commit";
import { asAck, okAck, failAck, type ActionAck } from "@/lib/actions/types";
import { actionDenied } from "@/lib/access/server";

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
  // Palmetto taxonomy (#160) — categorize touched rows plus any row a past sync never
  // categorized (never clears overrides). Mirrors skills/plaid_api/sync.py.
  try {
    const cat = await reapplyPlaidCategories({
      itemId,
      transactionIds: touchedIds.length ? touchedIds : undefined,
      includeUncategorized: true,
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
  // Concurrent MERGE races can leave duplicate transaction_id rows (Issue #230).
  try {
    const n = await dedupePlaidTransactions();
    if (n) console.info(`plaid dedupe removed=${n} item=${itemId}`);
  } catch (e) {
    console.error(
      `plaid dedupe failed item=${itemId}: ${e instanceof Error ? e.message : String(e)}`,
    );
  }
  return { added, modified, removed, cursor };
}

export async function createPlaidLinkTokenAction(): Promise<ActionAck<string>> {
  const denied = await actionDenied("createPlaidLinkTokenAction");
  if (denied) return denied;
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
  const denied = await actionDenied("exchangePlaidPublicTokenAction");
  if (denied) return denied;
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
  const denied = await actionDenied("syncPlaidNowAction");
  if (denied) return denied;
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
  const denied = await actionDenied("setPlaidInternalAction");
  if (denied) return denied;
  return asAck(async () => {
    if (!FEATURES.writePlaidLink) throw new Error("Plaid writes disabled");
    await operatorEmail();
    await setPlaidTransactionInternal(transactionId, isInternal);
    revalidatePath("/accounting");
  }, "Internal flag updated.");
}

/**
 * Full Reapply as a tracked job (plaid_categorize_jobs). Progress is polled from
 * GET /api/accounting/categorize-job while this runs; a second click while one is
 * live returns the running job instead of starting another writer.
 */
export async function reapplyPlaidCategoriesAction(): Promise<
  ActionAck<{ job: CategorizeJob; started: boolean }>
> {
  const denied = await actionDenied("reapplyPlaidCategoriesAction");
  if (denied) return denied;
  try {
    if (!FEATURES.writePlaidLink) throw new Error("Plaid writes disabled");
    const by = await operatorEmail();
    const out = await runCategorizeJob({ kind: "reapply", by });
    if (out.job.status === "failed") return failAck(new Error(`Reapply failed: ${out.job.message}`));
    revalidatePath("/accounting");
    revalidatePath("/home");
    return okAck({ data: out });
  } catch (e) {
    return failAck(e);
  }
}

export async function setTxnCategoryOverrideAction(
  transactionId: string,
  categoryId: string | null,
  subcategoryId: string | null,
): Promise<ActionAck> {
  const denied = await actionDenied("setTxnCategoryOverrideAction");
  if (denied) return denied;
  return asAck(async () => {
    if (!FEATURES.writePlaidLink) throw new Error("Plaid writes disabled");
    await operatorEmail();
    await dedupePlaidTransactions().catch((e) => {
      console.error(
        `plaid dedupe before category override: ${e instanceof Error ? e.message : String(e)}`,
      );
    });
    const label = `override txn=${transactionId}`;
    await withSerializeRetry(
      () => setPlaidTransactionOverride(transactionId, categoryId, subcategoryId),
      { label },
    );
    // If clearing override, restore rule result immediately.
    if (!categoryId) {
      await reapplyPlaidCategories({ transactionIds: [transactionId] });
    } else {
      // Mirror override into display columns for immediate UI consistency.
      await withSerializeRetry(() => mutate(
        `UPDATE ${plaidFq("plaid_transactions")}
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
      ), { label });
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
  const denied = await actionDenied("upsertTaxonomyNodeAction");
  if (denied) return denied;
  return asAck(async () => {
    if (!FEATURES.writePlaidLink) throw new Error("Plaid writes disabled");
    await operatorEmail();
    await mutate(
      `MERGE ${plaidFq("plaid_taxonomy_nodes")} T
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
  const denied = await actionDenied("setTaxonomyNodeEnabledAction");
  if (denied) return denied;
  return asAck(async () => {
    if (!FEATURES.writePlaidLink) throw new Error("Plaid writes disabled");
    await operatorEmail();
    await mutate(
      `UPDATE ${plaidFq("plaid_taxonomy_nodes")}
       SET enabled = @enabled, updated_at = CURRENT_TIMESTAMP()
       WHERE id = @id`,
      { id, enabled },
      { enabled: "BOOL" },
    );
    revalidatePath("/accounting");
  }, enabled ? "Node enabled." : "Node disabled.");
}

const RULE_TABLES = () => ({
  rules: plaidFq("plaid_category_rules"),
  events: plaidFq("plaid_category_rule_events"),
  txns: plaidFq("plaid_transactions"),
});

async function setRuleEnabled(id: string, enabled: boolean, action: string, by: string, note: string) {
  await withSerializeRetry(
    () =>
      mutate(
        setRuleEnabledScript(RULE_TABLES()),
        { id, enabled, action, note, by, event_id: randomUUID() },
        { enabled: "BOOL" },
      ),
    { label: `rule_${action} id=${id}` },
  );
}

/** Enable / disable only — takes effect on the next Reapply or ingest. */
export async function setCategoryRuleEnabledAction(
  id: string,
  enabled: boolean,
): Promise<ActionAck> {
  const denied = await actionDenied("setCategoryRuleEnabledAction");
  if (denied) return denied;
  return asAck(async () => {
    if (!FEATURES.writePlaidLink) throw new Error("Plaid writes disabled");
    const by = await operatorEmail();
    await setRuleEnabled(id, enabled, enabled ? "enable" : "disable", by, "Rules panel toggle");
    revalidatePath("/accounting");
  }, enabled ? "Rule enabled — Reapply to update past transactions." : "Rule disabled — Reapply or Revert to update past transactions.");
}

export type RuleDryRun = {
  rule_id: string;
  /** Rows the rule's criteria match (ignoring priority and overrides). */
  matches: number;
  /** Rows this rule would categorize on Reapply (wins priority, no override). */
  would_apply: number;
  /** Rows attributed to this rule right now. */
  currently_applied: number;
  /** Matching rows held by an operator override. */
  overrides: number;
  /** Rows a higher-priority rule takes first. */
  shadowed: number;
  sample: { date: string | null; name: string | null; amount: number; status: "applies" | "override" | "shadowed" }[];
};

/** Exact dry-run: the same evaluator Reapply uses (regex, direction, from/to last-4). */
export async function dryRunRuleAction(ruleId: string): Promise<ActionAck<RuleDryRun>> {
  const denied = await actionDenied("dryRunRuleAction");
  if (denied) return denied;
  return asAck(async () => {
    await operatorEmail();
    const [ruleRows, txns] = await Promise.all([
      loadRules({ includeDisabled: true }),
      loadTxnsForCategorize(),
    ]);
    const target = ruleRows.find((r) => r.id === ruleId);
    if (!target) throw new Error(`Rule ${ruleId} not found`);
    const probe = toCategoryRule(target, true);
    const active = ruleRows
      .filter((r) => r.id === ruleId || r.enabled !== false)
      .map((r) => toCategoryRule(r, r.id === ruleId));
    const out: RuleDryRun = {
      rule_id: ruleId,
      matches: 0,
      would_apply: 0,
      currently_applied: 0,
      overrides: 0,
      shadowed: 0,
      sample: [],
    };
    txns.sort((a, b) => (b.date || "").localeCompare(a.date || ""));
    for (const t of txns) {
      if (t.rule_id === ruleId) out.currently_applied += 1;
      const txn = {
        name: t.name,
        merchant_name: t.merchant_name,
        amount: t.amount,
        account_mask: t.account_mask,
        counterparty_name: t.counterparty_name,
      };
      if (!ruleMatches(txn, probe)) continue;
      out.matches += 1;
      let status: RuleDryRun["sample"][number]["status"];
      if (t.override_category_id) {
        out.overrides += 1;
        status = "override";
      } else if (evaluateRules(txn, active)?.rule_id === ruleId) {
        out.would_apply += 1;
        status = "applies";
      } else {
        out.shadowed += 1;
        status = "shadowed";
      }
      if (out.sample.length < 8) {
        out.sample.push({ date: t.date, name: t.name, amount: t.amount, status });
      }
    }
    return out;
  });
}

export type RuleDraft = {
  match_pattern: string;
  match_operator?: string;
  amount_sign?: string | null;
  /** Legacy linked-account last-4 filter. */
  account_mask?: string | null;
  from_mask?: string | null;
  to_mask?: string | null;
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

function draftMask(raw: string | null | undefined): string {
  return (raw || "").replace(/\D/g, "").slice(-4);
}

function draftHasCriteria(draft: RuleDraft): boolean {
  return Boolean(
    (draft.match_pattern || "").trim() ||
      draftMask(draft.from_mask) ||
      draftMask(draft.to_mask) ||
      draftMask(draft.account_mask),
  );
}

export async function previewRuleMatchesAction(
  draft: RuleDraft,
): Promise<ActionAck<RuleMatchPreview[]>> {
  const denied = await actionDenied("previewRuleMatchesAction");
  if (denied) return denied;
  return asAck(async () => {
    await operatorEmail();
    if (!draft.category_id || !draftHasCriteria(draft)) return [];
    await dedupePlaidTransactions().catch((e) => {
      console.error(
        `plaid dedupe before rule preview: ${e instanceof Error ? e.message : String(e)}`,
      );
    });
    const pattern = (draft.match_pattern || "").trim();
    const amountSign = draft.amount_sign || "any";
    const accountMask = draftMask(draft.account_mask);
    const fromMask = draftMask(draft.from_mask);
    const toMask = draftMask(draft.to_mask);
    const op = draft.match_operator || (pattern ? "regex" : "contains");

    // Loose SQL prefilter, then exact eval with directional from/to.
    const rows = await q<{
      transaction_id: string;
      date: string;
      name: string | null;
      merchant_name: string | null;
      amount: number;
      account_mask: string | null;
      counterparty_name: string | null;
      has_override: boolean;
    }>(
      `SELECT
         t.transaction_id,
         CAST(t.date AS STRING) AS date,
         t.name,
         t.merchant_name,
         t.amount,
         a.mask AS account_mask,
         JSON_VALUE(t.raw_json, '$.counterparties[0].name') AS counterparty_name,
         t.override_category_id IS NOT NULL AS has_override
       FROM ${plaidFq("plaid_transactions")} t
       LEFT JOIN \`jarvis-bhaga-prod.bhaga.plaid_accounts\` a ON a.account_id = t.account_id
       WHERE (
           @pattern = ''
           OR (
             @match_operator = 'regex'
             AND SAFE.REGEXP_CONTAINS(
               CONCAT(IFNULL(t.name,''),' ',IFNULL(t.merchant_name,'')),
               @pattern
             )
           )
           OR (
             @match_operator != 'regex'
             AND STRPOS(
               LOWER(CONCAT(IFNULL(t.name,''),' ',IFNULL(t.merchant_name,''))),
               LOWER(@pattern)
             ) > 0
           )
         )
         AND (
           @amount_sign = 'any'
           OR (@amount_sign = 'positive' AND t.amount > 0)
           OR (@amount_sign = 'negative' AND t.amount < 0)
         )
         AND (
           @account_mask = ''
           OR RIGHT(REGEXP_REPLACE(IFNULL(a.mask,''), r'[^0-9]', ''), 4) = @account_mask
         )
         AND (
           (@from_mask = '' AND @to_mask = '')
           OR (
             @from_mask != ''
             AND (
               RIGHT(REGEXP_REPLACE(IFNULL(a.mask,''), r'[^0-9]', ''), 4) = @from_mask
               OR STRPOS(IFNULL(t.name,''), @from_mask) > 0
             )
           )
           OR (
             @to_mask != ''
             AND (
               RIGHT(REGEXP_REPLACE(IFNULL(a.mask,''), r'[^0-9]', ''), 4) = @to_mask
               OR STRPOS(IFNULL(t.name,''), @to_mask) > 0
             )
           )
         )
       QUALIFY ROW_NUMBER() OVER (
         PARTITION BY t.transaction_id
         ORDER BY t.updated_at DESC NULLS LAST
       ) = 1
       ORDER BY t.date DESC
       LIMIT 2000`,
      {
        pattern,
        match_operator: op,
        amount_sign: amountSign,
        account_mask: accountMask,
        from_mask: fromMask,
        to_mask: toMask,
      },
    );

    const probe = {
      id: "preview",
      priority: 1,
      match_field: "name_or_merchant" as const,
      match_operator: op as "regex" | "contains",
      match_pattern: pattern,
      amount_sign: amountSign as "any" | "positive" | "negative",
      category_id: draft.category_id,
      subcategory_id: draft.subcategory_id ?? null,
      enabled: true,
      account_mask: accountMask || null,
      from_mask: fromMask || null,
      to_mask: toMask || null,
    };

    return rows
      .filter((r) =>
        ruleMatches(
          {
            name: r.name,
            merchant_name: r.merchant_name,
            amount: Number(r.amount),
            account_mask: r.account_mask,
            counterparty_name: r.counterparty_name,
          },
          probe,
        ),
      )
      .slice(0, 500)
      .map((r) => ({
        transaction_id: r.transaction_id,
        date: r.date,
        name: r.name,
        amount: Number(r.amount),
        has_override: Boolean(r.has_override),
      }));
  });
}

/**
 * Commit a proposed rule — all or nothing. Refused (nothing written) while a Reapply
 * job is live; otherwise one transaction inserts the rule + history event and backfills
 * the selected rows, retried on serialization conflicts. An identical enabled rule is
 * reused, so a retry or double-submit never adds a duplicate. With "Apply to future"
 * on, a rule may be committed with zero selected rows (single-occurrence merchants).
 */
export async function commitRuleFromTxnAction(input: {
  draft: RuleDraft;
  selectedTxnIds: string[];
  applyFuture: boolean;
}): Promise<
  ActionAck<{ ruleId: string; applied: number; skipped_override: number; reused: boolean }>
> {
  const denied = await actionDenied("commitRuleFromTxnAction");
  if (denied) return denied;
  try {
    if (!FEATURES.writePlaidLink) throw new Error("Plaid writes disabled");
    const by = await operatorEmail();
    const pattern = (input.draft.match_pattern || "").trim();
    if (!input.draft.category_id || !draftHasCriteria(input.draft)) {
      throw new Error("category_id and at least one of name pattern / from / to required");
    }
    const selectedIds = [...new Set(input.selectedTxnIds.filter(Boolean))];
    if (!selectedIds.length && input.applyFuture === false) {
      throw new Error("Nothing to commit — select matches or turn on Apply to future transactions.");
    }
    const busy = await busyMessage("Committing a rule");
    if (busy) throw new Error(busy);

    const criteria = {
      match_pattern: pattern,
      match_operator: input.draft.match_operator || (pattern ? "regex" : "contains"),
      amount_sign: input.draft.amount_sign || "any",
      account_mask: draftMask(input.draft.account_mask) || null,
      from_mask: draftMask(input.draft.from_mask) || null,
      to_mask: draftMask(input.draft.to_mask) || null,
      category_id: input.draft.category_id,
      subcategory_id: input.draft.subcategory_id || null,
    };
    const slugSrc = pattern || criteria.from_mask || criteria.to_mask || criteria.account_mask || "rule";
    const slug = slugSrc
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_|_$/g, "")
      .slice(0, 40);
    const newId = `op_${slug || "rule"}_${Date.now().toString(36)}`;

    let skipped_override = 0;
    if (selectedIds.length) {
      const skipRows = await q<{ n: number }>(
        `SELECT COUNT(*) AS n
         FROM ${plaidFq("plaid_transactions")}
         WHERE transaction_id IN UNNEST(@ids)
           AND override_category_id IS NOT NULL`,
        { ids: selectedIds },
      );
      skipped_override = Number(skipRows[0]?.n ?? 0);
    }

    let ruleId = newId;
    let reused = false;
    await withSerializeRetry(
      async () => {
        const existing = findIdenticalRule(
          (await loadRules()).map((r) => ({ ...r, priority: Number(r.priority) })),
          criteria,
        );
        reused = Boolean(existing);
        ruleId = existing?.id ?? newId;
        await mutate(
          commitRuleScript(RULE_TABLES()),
          {
            ...criteria,
            create: !existing,
            id: ruleId,
            enabled: input.applyFuture !== false,
            event_id: randomUUID(),
            by,
            ids: selectedIds,
          },
          {
            account_mask: "STRING",
            from_mask: "STRING",
            to_mask: "STRING",
            subcategory_id: "STRING",
            enabled: "BOOL",
            create: "BOOL",
            ids: ["STRING"],
          },
        );
      },
      { label: `rule_commit id=${newId} selected=${selectedIds.length}` },
    );
    const applied = Math.max(0, selectedIds.length - skipped_override);
    console.info(
      `plaid_rule_commit ok rule=${ruleId} reused=${reused} applied=${applied} ` +
        `skipped_override=${skipped_override} future=${input.applyFuture !== false}`,
    );
    revalidatePath("/accounting");
    revalidatePath("/home");
    return okAck({
      data: { ruleId, applied, skipped_override, reused },
      message: reused
        ? `Identical rule ${ruleId} already exists — applied it to ${applied}.`
        : applied
          ? `Rule committed — applied ${applied}.`
          : "Rule committed — it will apply to future transactions.",
    });
  } catch (e) {
    console.error(`plaid_rule_commit failed: ${e instanceof Error ? e.message : String(e)}`);
    return failAck(e);
  }
}

/**
 * Revert a rule: disable it (history event), then re-categorize only the rows it
 * currently holds — they fall to the next matching rule or back to Uncategorized.
 */
export async function revertRuleEvidenceAction(
  ruleId: string,
): Promise<ActionAck<{ job: CategorizeJob; started: boolean }>> {
  const denied = await actionDenied("revertRuleEvidenceAction");
  if (denied) return denied;
  try {
    if (!FEATURES.writePlaidLink) throw new Error("Plaid writes disabled");
    const by = await operatorEmail();
    const out = await runCategorizeJob({
      kind: "revert_rule",
      by,
      ruleId,
      scope: { ruleId },
      before: () => setRuleEnabled(ruleId, false, "revert", by, "Revert from console"),
    });
    if (!out.started) {
      return failAck(new Error(await busyMessage("Reverting a rule") ?? "Another job is running."));
    }
    if (out.job.status === "failed") return failAck(new Error(`Revert failed: ${out.job.message}`));
    revalidatePath("/accounting");
    revalidatePath("/home");
    return okAck({ data: out, message: `Rule ${ruleId} reverted.` });
  } catch (e) {
    return failAck(e);
  }
}

export async function setTaxonomyExcludeAction(
  id: string,
  excludeFromAccounting: boolean | null,
): Promise<ActionAck> {
  const denied = await actionDenied("setTaxonomyExcludeAction");
  if (denied) return denied;
  return asAck(async () => {
    if (!FEATURES.writePlaidLink) throw new Error("Plaid writes disabled");
    await operatorEmail();
    await mutate(
      `UPDATE ${plaidFq("plaid_taxonomy_nodes")}
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
