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
  setPlaidTransactionInternal,
  updatePlaidCursor,
  upsertPlaidAccount,
  upsertPlaidItem,
  upsertPlaidTransaction,
  type PlaidTxnWrite,
} from "@/lib/bq/writes";
import { plaidItems } from "@/lib/bq/queries";

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
  for (;;) {
    const page = await transactionsSync(accessToken, cursor || null);
    for (const t of page.added) {
      await upsertPlaidTransaction(txnToWrite(t, itemId));
      added += 1;
    }
    for (const t of page.modified) {
      await upsertPlaidTransaction(txnToWrite(t, itemId));
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
  return { added, modified, removed, cursor };
}

export async function createPlaidLinkTokenAction(): Promise<string> {
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
}

export async function exchangePlaidPublicTokenAction(publicToken: string): Promise<{
  itemId: string;
  sync: { added: number; modified: number; removed: number };
}> {
  if (!FEATURES.writePlaidLink) throw new Error("Plaid Link is disabled");
  const by = await operatorEmail();
  const { access_token, item_id } = await exchangePublicToken(publicToken);
  await saveAccessTokenSecret(item_id, access_token);
  const institutionName = await fetchInstitutionName(access_token);
  await upsertPlaidItem(DEFAULT_STORE, item_id, institutionName, by);
  const sync = await drainSync(item_id, access_token, "");
  revalidatePath("/accounting");
  return { itemId: item_id, sync };
}

export async function syncPlaidNowAction(): Promise<{
  itemId: string;
  sync: { added: number; modified: number; removed: number };
}> {
  if (!FEATURES.writePlaidLink) throw new Error("Plaid sync is disabled");
  await operatorEmail();
  const items = await plaidItems(DEFAULT_STORE);
  const item = items[0];
  if (!item) throw new Error("No linked Plaid Item — Link a bank first");
  const accessToken = await loadAccessTokenSecret(item.item_id);
  const sync = await drainSync(item.item_id, accessToken, item.cursor || "");
  revalidatePath("/accounting");
  return { itemId: item.item_id, sync };
}

export async function setPlaidInternalAction(
  transactionId: string,
  isInternal: boolean,
): Promise<void> {
  if (!FEATURES.writePlaidLink) throw new Error("Plaid writes disabled");
  await operatorEmail();
  await setPlaidTransactionInternal(transactionId, isInternal);
  revalidatePath("/accounting");
}
