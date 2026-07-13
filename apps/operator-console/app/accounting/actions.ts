"use server";

import { revalidatePath } from "next/cache";
import { operatorEmail, DEFAULT_STORE } from "@/lib/auth/identity";
import { FEATURES } from "@/lib/config/features";
import {
  createLinkToken,
  exchangePublicToken,
  loadAccessTokenSecret,
  saveAccessTokenSecret,
  transactionsSync,
} from "@/lib/plaid/client";
import {
  deletePlaidTransactions,
  updatePlaidCursor,
  upsertPlaidItem,
  upsertPlaidTransaction,
  type PlaidTxnWrite,
} from "@/lib/bq/writes";
import { plaidItems } from "@/lib/bq/queries";

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
  return { added, modified, removed, cursor };
}

export async function createPlaidLinkTokenAction(): Promise<string> {
  if (!FEATURES.writePlaidLink) throw new Error("Plaid Link is disabled (FEATURES.writePlaidLink)");
  const email = await operatorEmail();
  const webhook = process.env.PLAID_WEBHOOK_URL?.trim() || undefined;
  return createLinkToken(email, webhook);
}

export async function exchangePlaidPublicTokenAction(publicToken: string): Promise<{
  itemId: string;
  sync: { added: number; modified: number; removed: number };
}> {
  if (!FEATURES.writePlaidLink) throw new Error("Plaid Link is disabled");
  const by = await operatorEmail();
  const { access_token, item_id } = await exchangePublicToken(publicToken);
  await saveAccessTokenSecret(item_id, access_token);
  await upsertPlaidItem(DEFAULT_STORE, item_id, null, by);
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
