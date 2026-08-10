"use server";

import { revalidatePath } from "next/cache";
import { operatorEmail, DEFAULT_STORE } from "@/lib/auth/identity";
import {
  submitRestock,
  setConfig,
  replaceEstimatedRestockDate,
  moveRestockDate,
  removeRestockDate,
  setUsageDayOverride,
  clearUsageDayOverride,
  readUsageDayAuditRow,
  replaceOrderTubOverrides,
  setCurrentQtyOverride,
  clearCurrentQtyOverride,
  applyCurrentQtyOverrides,
  clearCurrentQtyOverrides,
  type RestockAction,
  type UsageDayOverrideMode,
} from "@/lib/bq/writes";
import { orderRecoRefreshedAt } from "@/lib/bq/queries";
import { orderRecoRefreshedAdvanced } from "@/lib/inventory/orderRecoFreshness";
import type { RestockRow } from "@/lib/restock/parse";
import { okAck, failAck, type ActionAck } from "@/lib/actions/types";
import { FEATURES } from "@/lib/config/features";
import { triggerOrderRecoRefresh } from "@/lib/bhaga/recompute";

async function maybeQueueOrderReco(): Promise<string[] | undefined> {
  if (!FEATURES.asyncOrderReco) return undefined;
  await triggerOrderRecoRefresh(DEFAULT_STORE);
  return ["order-reco"];
}

export type OrderRecoQueuedMeta = {
  baselineRefreshedAt: string | null;
};

/**
 * Prod: skip inline TVFs and enqueue Cloud Run (Issue #175).
 * Local BYPASS_IAP dogfood: run refresh inline so Inventory updates without a job.
 */
function shouldSkipInlineOrderReco(): boolean {
  const syncLocal = Boolean(process.env.BYPASS_IAP_EMAIL?.trim());
  return FEATURES.asyncOrderReco && !syncLocal;
}

/** Capture refreshed_at, then enqueue (client polls until it advances). */
async function queueOrderRecoWithBaseline(): Promise<{
  queued: string[] | undefined;
  baselineRefreshedAt: string | null;
}> {
  const baselineRefreshedAt = await orderRecoRefreshedAt(DEFAULT_STORE);
  const queued = await maybeQueueOrderReco();
  return { queued, baselineRefreshedAt };
}

async function finishOrderRecoWrite(
  skipRefresh: boolean,
  messages: { done: string; queued: string },
): Promise<ActionAck<OrderRecoQueuedMeta>> {
  if (skipRefresh) {
    const { queued, baselineRefreshedAt } = await queueOrderRecoWithBaseline();
    revalidatePath("/inventory");
    return okAck({
      message: queued ? messages.queued : messages.done,
      queued,
      data: { baselineRefreshedAt },
    });
  }
  revalidatePath("/inventory");
  return okAck({
    message: messages.done,
    data: { baselineRefreshedAt: await orderRecoRefreshedAt(DEFAULT_STORE) },
  });
}

export async function submitRestockAction(
  deliveryDate: string,
  action: RestockAction,
  rows: RestockRow[],
): Promise<ActionAck<OrderRecoQueuedMeta>> {
  try {
    const by = await operatorEmail();
    const skipRefresh = shouldSkipInlineOrderReco();
    await submitRestock(DEFAULT_STORE, deliveryDate, action, rows, by, {
      skipRefresh,
    });
    return finishOrderRecoWrite(skipRefresh, {
      done: "Restock saved.",
      queued: "Restock saved — recommendation refreshing…",
    });
  } catch (e) {
    return failAck(e);
  }
}

/** Console-only: move an Estimated schedule date and refresh dual-date reco. */
export async function replaceEstimatedRestockDateAction(
  fromDate: string,
  toDate: string,
): Promise<ActionAck<OrderRecoQueuedMeta>> {
  try {
    const by = await operatorEmail();
    const skipRefresh = shouldSkipInlineOrderReco();
    await replaceEstimatedRestockDate(DEFAULT_STORE, fromDate, toDate, by, {
      skipRefresh,
    });
    return finishOrderRecoWrite(skipRefresh, {
      done: "Date replaced.",
      queued: "Date replaced — recommendation refreshing…",
    });
  } catch (e) {
    return failAck(e);
  }
}

/** Console-only: move schedule (+ Actuals / Manual pins) from → to. */
export async function moveRestockDateAction(
  fromDate: string,
  toDate: string,
): Promise<ActionAck<OrderRecoQueuedMeta>> {
  try {
    const by = await operatorEmail();
    const skipRefresh = shouldSkipInlineOrderReco();
    await moveRestockDate(DEFAULT_STORE, fromDate, toDate, by, {
      skipRefresh,
    });
    return finishOrderRecoWrite(skipRefresh, {
      done: "Date moved.",
      queued: "Date moved — recommendation refreshing…",
    });
  } catch (e) {
    return failAck(e);
  }
}

/** Console-only: remove a registered delivery date entirely. */
export async function removeRestockDateAction(
  deliveryDate: string,
): Promise<ActionAck<OrderRecoQueuedMeta>> {
  try {
    const by = await operatorEmail();
    const skipRefresh = shouldSkipInlineOrderReco();
    await removeRestockDate(DEFAULT_STORE, deliveryDate, by, {
      skipRefresh,
    });
    return finishOrderRecoWrite(skipRefresh, {
      done: "Date removed.",
      queued: "Date removed — recommendation refreshing…",
    });
  } catch (e) {
    return failAck(e);
  }
}

export async function setCapacityAction(
  maxTubs: number,
): Promise<ActionAck<OrderRecoQueuedMeta>> {
  try {
    const by = await operatorEmail();
    const skipRefresh = shouldSkipInlineOrderReco();
    await setConfig(DEFAULT_STORE, "order_reco_max_tubs", String(maxTubs), by, {
      skipRefresh,
    });
    return finishOrderRecoWrite(skipRefresh, {
      done: "Capacity saved.",
      queued: "Capacity saved — recommendation refreshing…",
    });
  } catch (e) {
    return failAck(e);
  }
}

export async function setUsageDayOverrideAction(
  item: string,
  submittedDate: string,
  mode: UsageDayOverrideMode,
): Promise<ActionAck> {
  if (!FEATURES.writeInventoryDayOverrides) {
    return failAck(new Error("Usage day overrides are disabled"));
  }
  try {
    const by = await operatorEmail();
    await setUsageDayOverride(DEFAULT_STORE, item, submittedDate, mode, by);
    const queued = await maybeQueueOrderReco();
    const preview = await readUsageDayAuditRow(DEFAULT_STORE, item, submittedDate);
    revalidatePath("/inventory");
    return okAck({
      message: queued
        ? `Override ${mode} saved — recommendation refreshing…`
        : `Override ${mode} saved.`,
      queued,
      data: preview,
    });
  } catch (e) {
    return failAck(e);
  }
}

export async function clearUsageDayOverrideAction(
  item: string,
  submittedDate: string,
): Promise<ActionAck> {
  if (!FEATURES.writeInventoryDayOverrides) {
    return failAck(new Error("Usage day overrides are disabled"));
  }
  try {
    await operatorEmail();
    await clearUsageDayOverride(DEFAULT_STORE, item, submittedDate);
    const queued = await maybeQueueOrderReco();
    const preview = await readUsageDayAuditRow(DEFAULT_STORE, item, submittedDate);
    revalidatePath("/inventory");
    return okAck({
      message: queued ? "Override cleared — recommendation refreshing…" : "Override cleared.",
      queued,
      data: preview,
    });
  } catch (e) {
    return failAck(e);
  }
}

export type UsageDayOverrideDraft = {
  item: string;
  /** `rule` clears any sticky override. */
  mode: UsageDayOverrideMode | "rule";
};

export type ApplyUsageDayOverridesResult = {
  baselineRefreshedAt: string | null;
};

/** Batch apply drafts for one date — single reco refresh (Issue #194 drawer). */
export async function applyUsageDayOverridesAction(
  submittedDate: string,
  changes: UsageDayOverrideDraft[],
): Promise<ActionAck<ApplyUsageDayOverridesResult>> {
  if (!FEATURES.writeInventoryDayOverrides) {
    return failAck(new Error("Usage day overrides are disabled"));
  }
  if (!changes.length) {
    return okAck({ message: "No changes." });
  }
  try {
    const by = await operatorEmail();
    // Capture before enqueue so the client can poll until materialization advances.
    const baselineRefreshedAt = await orderRecoRefreshedAt(DEFAULT_STORE);
    for (const c of changes) {
      if (c.mode === "rule") {
        await clearUsageDayOverride(DEFAULT_STORE, c.item, submittedDate);
      } else {
        await setUsageDayOverride(DEFAULT_STORE, c.item, submittedDate, c.mode, by);
      }
    }
    const queued = await maybeQueueOrderReco();
    revalidatePath("/inventory");
    return okAck({
      message: queued
        ? `Saved ${changes.length} override(s) — averages updating…`
        : `Saved ${changes.length} override(s).`,
      queued,
      data: { baselineRefreshedAt },
    });
  } catch (e) {
    return failAck(e);
  }
}

export async function applyOrderTubOverridesAction(
  deliveryDate: string,
  rows: { item: string; quantityTubs: number }[],
): Promise<ActionAck<OrderRecoQueuedMeta>> {
  if (!FEATURES.writeRestock) {
    return failAck(new Error("Order tub overrides are disabled"));
  }
  try {
    const by = await operatorEmail();
    // Local BYPASS_IAP dogfood: sync recompute so Apply shows new tubs without
    // waiting on Cloud Run (prod keeps asyncOrderReco enqueue).
    const skipRefresh = shouldSkipInlineOrderReco();
    await replaceOrderTubOverrides(DEFAULT_STORE, deliveryDate, rows, by, {
      skipRefresh,
    });
    return finishOrderRecoWrite(skipRefresh, {
      done: "Estimate pins saved.",
      queued: "Estimate pins saved — recommendation refreshing…",
    });
  } catch (e) {
    return failAck(e);
  }
}

/** Issue #240 — sticky Current Qty override (gated like Order Tubs). */
export async function setCurrentQtyOverrideAction(
  item: string,
  quantityUnits: number,
): Promise<ActionAck<OrderRecoQueuedMeta>> {
  if (!FEATURES.writeRestock) {
    return failAck(new Error("Current Qty overrides are disabled"));
  }
  try {
    const by = await operatorEmail();
    // Always rematerialize inline — On Hand / Order Tubs / Days left / runway
    // all derive from current_qty; async enqueue would leave the table stale.
    await setCurrentQtyOverride(DEFAULT_STORE, item, quantityUnits, by, {
      skipRefresh: false,
    });
    return finishOrderRecoWrite(false, {
      done: "Current Qty saved — order reco recalculated.",
      queued: "Current Qty saved — recommendation refreshing…",
    });
  } catch (e) {
    return failAck(e);
  }
}

export async function clearCurrentQtyOverrideAction(
  item: string,
): Promise<ActionAck<OrderRecoQueuedMeta>> {
  if (!FEATURES.writeRestock) {
    return failAck(new Error("Current Qty overrides are disabled"));
  }
  try {
    await clearCurrentQtyOverride(DEFAULT_STORE, item, { skipRefresh: false });
    return finishOrderRecoWrite(false, {
      done: "Current Qty reset — order reco recalculated.",
      queued: "Current Qty reset — recommendation refreshing…",
    });
  } catch (e) {
    return failAck(e);
  }
}

/** Batch Apply for Current Qty Sheet (all dirty bases). */
export async function applyCurrentQtyOverridesAction(
  rows: { item: string; quantityUnits: number }[],
): Promise<ActionAck<OrderRecoQueuedMeta>> {
  if (!FEATURES.writeRestock) {
    return failAck(new Error("Current Qty overrides are disabled"));
  }
  try {
    const by = await operatorEmail();
    // Always rematerialize inline — see setCurrentQtyOverrideAction.
    await applyCurrentQtyOverrides(DEFAULT_STORE, rows, by, { skipRefresh: false });
    return finishOrderRecoWrite(false, {
      done: "Current Qty saved — order reco recalculated.",
      queued: "Current Qty saved — recommendation refreshing…",
    });
  } catch (e) {
    return failAck(e);
  }
}

/** Reset many bases to ClickUp closings. */
export async function clearCurrentQtyOverridesAction(
  items: string[],
): Promise<ActionAck<OrderRecoQueuedMeta>> {
  if (!FEATURES.writeRestock) {
    return failAck(new Error("Current Qty overrides are disabled"));
  }
  try {
    await clearCurrentQtyOverrides(DEFAULT_STORE, items, { skipRefresh: false });
    return finishOrderRecoWrite(false, {
      done: "Current Qty reset — order reco recalculated.",
      queued: "Current Qty reset — recommendation refreshing…",
    });
  } catch (e) {
    return failAck(e);
  }
}

export type OrderRecoRefreshPoll = {
  refreshedAt: string | null;
  advanced: boolean;
};

/** Poll inventory_order_reco.refreshed_at after async order-reco enqueue. */
export async function pollOrderRecoRefreshAction(opts: {
  baselineRefreshedAt: string | null;
}): Promise<ActionAck<OrderRecoRefreshPoll>> {
  try {
    const refreshedAt = await orderRecoRefreshedAt(DEFAULT_STORE);
    return okAck({
      data: {
        refreshedAt,
        advanced: orderRecoRefreshedAdvanced(opts.baselineRefreshedAt, refreshedAt),
      },
    });
  } catch (e) {
    return failAck(e);
  }
}
