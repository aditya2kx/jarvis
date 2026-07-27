"use server";

import { revalidatePath } from "next/cache";
import { operatorEmail, DEFAULT_STORE } from "@/lib/auth/identity";
import {
  submitRestock,
  setConfig,
  replaceEstimatedRestockDate,
  setUsageDayOverride,
  clearUsageDayOverride,
  readUsageDayAuditRow,
  type RestockAction,
  type UsageDayOverrideMode,
} from "@/lib/bq/writes";
import type { RestockRow } from "@/lib/restock/parse";
import { okAck, failAck, type ActionAck } from "@/lib/actions/types";
import { FEATURES } from "@/lib/config/features";
import { triggerOrderRecoRefresh } from "@/lib/bhaga/recompute";

async function maybeQueueOrderReco(): Promise<string[] | undefined> {
  if (!FEATURES.asyncOrderReco) return undefined;
  await triggerOrderRecoRefresh(DEFAULT_STORE);
  return ["order-reco"];
}

export async function submitRestockAction(
  deliveryDate: string,
  action: RestockAction,
  rows: RestockRow[],
): Promise<ActionAck> {
  try {
    const by = await operatorEmail();
    await submitRestock(DEFAULT_STORE, deliveryDate, action, rows, by, {
      skipRefresh: FEATURES.asyncOrderReco,
    });
    const queued = await maybeQueueOrderReco();
    revalidatePath("/inventory");
    return okAck({
      message: queued ? "Restock saved — recommendation refreshing…" : "Restock saved.",
      queued,
    });
  } catch (e) {
    return failAck(e);
  }
}

/** Console-only: move an Estimated schedule date and refresh dual-date reco. */
export async function replaceEstimatedRestockDateAction(
  fromDate: string,
  toDate: string,
): Promise<ActionAck> {
  try {
    const by = await operatorEmail();
    await replaceEstimatedRestockDate(DEFAULT_STORE, fromDate, toDate, by, {
      skipRefresh: FEATURES.asyncOrderReco,
    });
    const queued = await maybeQueueOrderReco();
    revalidatePath("/inventory");
    return okAck({
      message: queued ? "Date replaced — recommendation refreshing…" : "Date replaced.",
      queued,
    });
  } catch (e) {
    return failAck(e);
  }
}

export async function setCapacityAction(maxTubs: number): Promise<ActionAck> {
  try {
    const by = await operatorEmail();
    await setConfig(DEFAULT_STORE, "order_reco_max_tubs", String(maxTubs), by, {
      skipRefresh: FEATURES.asyncOrderReco,
    });
    const queued = await maybeQueueOrderReco();
    revalidatePath("/inventory");
    return okAck({
      message: queued ? "Capacity saved — recommendation refreshing…" : "Capacity saved.",
      queued,
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

/** Batch apply drafts for one date — single reco refresh (Issue #194 drawer). */
export async function applyUsageDayOverridesAction(
  submittedDate: string,
  changes: UsageDayOverrideDraft[],
): Promise<ActionAck> {
  if (!FEATURES.writeInventoryDayOverrides) {
    return failAck(new Error("Usage day overrides are disabled"));
  }
  if (!changes.length) {
    return okAck({ message: "No changes." });
  }
  try {
    const by = await operatorEmail();
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
    });
  } catch (e) {
    return failAck(e);
  }
}
