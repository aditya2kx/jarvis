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
import {
  orderRecoLastChange,
  orderRecoStatus,
  requestOrderRecoRefresh,
} from "@/lib/bhaga/orderReco";
import type { OrderRecoChange, OrderRecoStatus } from "@/lib/inventory/orderRecoStatus";
import type { RestockRow } from "@/lib/restock/parse";
import { okAck, failAck, type ActionAck } from "@/lib/actions/types";
import { FEATURES } from "@/lib/config/features";

const QUEUED = ["order-reco"];

export type OrderRecoQueuedMeta = {
  /** The refresh this write started (null on the legacy Cloud Run path). */
  runId: string | null;
};

/** Every inventory input write ends here: one refresh for the whole edit. */
async function finishOrderRecoWrite(
  trigger: string,
  by: string,
  message: string,
): Promise<ActionAck<OrderRecoQueuedMeta>> {
  const { runId } = await requestOrderRecoRefresh({ store: DEFAULT_STORE, trigger, requestedBy: by });
  revalidatePath("/inventory");
  return okAck({ message: `${message} — recommendation updating…`, queued: QUEUED, data: { runId } });
}

export async function submitRestockAction(
  deliveryDate: string,
  action: RestockAction,
  rows: RestockRow[],
): Promise<ActionAck<OrderRecoQueuedMeta>> {
  try {
    const by = await operatorEmail();
    await submitRestock(DEFAULT_STORE, deliveryDate, action, rows, by);
    return finishOrderRecoWrite(`restock-${action}`, by, "Restock saved");
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
    await replaceEstimatedRestockDate(DEFAULT_STORE, fromDate, toDate, by);
    return finishOrderRecoWrite("restock-replace-estimated", by, "Date replaced");
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
    await moveRestockDate(DEFAULT_STORE, fromDate, toDate, by);
    return finishOrderRecoWrite("restock-move-date", by, "Date moved");
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
    await removeRestockDate(DEFAULT_STORE, deliveryDate, by);
    return finishOrderRecoWrite("restock-remove-date", by, "Date removed");
  } catch (e) {
    return failAck(e);
  }
}

export async function setCapacityAction(
  maxTubs: number,
): Promise<ActionAck<OrderRecoQueuedMeta>> {
  try {
    const by = await operatorEmail();
    await setConfig(DEFAULT_STORE, "order_reco_max_tubs", String(maxTubs), by);
    return finishOrderRecoWrite("capacity", by, "Capacity saved");
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
    await requestOrderRecoRefresh({ store: DEFAULT_STORE, trigger: "usage-day", requestedBy: by });
    const preview = await readUsageDayAuditRow(DEFAULT_STORE, item, submittedDate);
    revalidatePath("/inventory");
    return okAck({
      message: `Override ${mode} saved — recommendation updating…`,
      queued: QUEUED,
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
    const by = await operatorEmail();
    await clearUsageDayOverride(DEFAULT_STORE, item, submittedDate, by);
    await requestOrderRecoRefresh({ store: DEFAULT_STORE, trigger: "usage-day", requestedBy: by });
    const preview = await readUsageDayAuditRow(DEFAULT_STORE, item, submittedDate);
    revalidatePath("/inventory");
    return okAck({
      message: "Override cleared — recommendation updating…",
      queued: QUEUED,
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

/** Batch apply drafts for one date — single reco refresh (Issue #194 drawer). */
export async function applyUsageDayOverridesAction(
  submittedDate: string,
  changes: UsageDayOverrideDraft[],
): Promise<ActionAck<OrderRecoQueuedMeta>> {
  if (!changes.length) {
  if (!FEATURES.writeInventoryDayOverrides) {
    return failAck(new Error("Usage day overrides are disabled"));
  }
    return okAck({ message: "No changes." });
  }
  try {
    const by = await operatorEmail();
    for (const c of changes) {
      if (c.mode === "rule") {
        await clearUsageDayOverride(DEFAULT_STORE, c.item, submittedDate, by);
      } else {
        await setUsageDayOverride(DEFAULT_STORE, c.item, submittedDate, c.mode, by);
      }
    }
    return finishOrderRecoWrite("usage-day", by, `Saved ${changes.length} override(s)`);
  } catch (e) {
    return failAck(e);
  }
}

export async function applyOrderTubOverridesAction(
  deliveryDate: string,
  rows: { item: string; quantityTubs: number }[],
): Promise<ActionAck<OrderRecoQueuedMeta>> {
  try {
    const by = await operatorEmail();
    await replaceOrderTubOverrides(DEFAULT_STORE, deliveryDate, rows, by);
    return finishOrderRecoWrite("order-tub-pins", by, "Estimate pins saved");
  } catch (e) {
  if (!FEATURES.writeRestock) {
    return failAck(new Error("Order tub overrides are disabled"));
  }
    return failAck(e);
  }
}

/** Issue #240 — sticky Current Qty override. */
export async function setCurrentQtyOverrideAction(
  item: string,
  quantityUnits: number,
): Promise<ActionAck<OrderRecoQueuedMeta>> {
  try {
    const by = await operatorEmail();
    await setCurrentQtyOverride(DEFAULT_STORE, item, quantityUnits, by);
    return finishOrderRecoWrite("current-qty", by, "Current Qty saved");
  } catch (e) {
  if (!FEATURES.writeRestock) {
    return failAck(new Error("Current Qty overrides are disabled"));
  }
    return failAck(e);
  }
}

export async function clearCurrentQtyOverrideAction(
  item: string,
): Promise<ActionAck<OrderRecoQueuedMeta>> {
  try {
    const by = await operatorEmail();
    await clearCurrentQtyOverride(DEFAULT_STORE, item, by);
    return finishOrderRecoWrite("current-qty", by, "Current Qty reset");
  } catch (e) {
  if (!FEATURES.writeRestock) {
    return failAck(new Error("Current Qty overrides are disabled"));
  }
    return failAck(e);
  }
}

/** Batch Apply for Current Qty Sheet (all dirty bases). */
export async function applyCurrentQtyOverridesAction(
  rows: { item: string; quantityUnits: number }[],
): Promise<ActionAck<OrderRecoQueuedMeta>> {
  try {
    const by = await operatorEmail();
    await applyCurrentQtyOverrides(DEFAULT_STORE, rows, by);
    return finishOrderRecoWrite("current-qty", by, "Current Qty saved");
  } catch (e) {
  if (!FEATURES.writeRestock) {
    return failAck(new Error("Current Qty overrides are disabled"));
  }
    return failAck(e);
  }
}

/** Reset many bases to ClickUp closings. */
export async function clearCurrentQtyOverridesAction(
  items: string[],
): Promise<ActionAck<OrderRecoQueuedMeta>> {
  try {
    const by = await operatorEmail();
    await clearCurrentQtyOverrides(DEFAULT_STORE, items, by);
    return finishOrderRecoWrite("current-qty", by, "Current Qty reset");
  } catch (e) {
  if (!FEATURES.writeRestock) {
    return failAck(new Error("Current Qty overrides are disabled"));
  }
    return failAck(e);
  }
}

export type OrderRecoStatusPoll = {
  status: OrderRecoStatus;
  /** Per-date TOTAL tubs before/after the latest refresh (only when requested). */
  changes?: OrderRecoChange[];
};

/** Page-level poller for the refresh banner (OrderRecoStatusProvider). */
export async function orderRecoStatusAction(opts?: {
  withChanges?: boolean;
  awaitRunId?: string | null;
}): Promise<ActionAck<OrderRecoStatusPoll>> {
  try {
    const [status, changes] = await Promise.all([
      orderRecoStatus(DEFAULT_STORE, opts?.awaitRunId),
      opts?.withChanges ? orderRecoLastChange(DEFAULT_STORE) : Promise.resolve(undefined),
    ]);
    return okAck({ data: { status, changes } });
  } catch (e) {
    return failAck(e);
  }
}

/** Banner Retry after a failed refresh. */
export async function retryOrderRecoAction(): Promise<ActionAck<OrderRecoQueuedMeta>> {
  try {
    const by = await operatorEmail();
    const { runId } = await requestOrderRecoRefresh({ store: DEFAULT_STORE, trigger: "retry", requestedBy: by });
    return okAck({ message: "Retrying recommendation refresh…", queued: QUEUED, data: { runId } });
  } catch (e) {
    return failAck(e);
  }
}
