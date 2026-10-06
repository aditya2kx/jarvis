import "server-only";
import { adpHoursScrapedAt } from "@/lib/bq/queries";
import {
  getCloudRunExecutionStatus,
  runningAdpSyncExecution,
  triggerAdpSync,
  type CloudRunExecutionStatus,
} from "@/lib/bhaga/recompute";
import { chicagoTodayIso, shiftCalendarDate } from "@/lib/filters/range";

const ISO = /^\d{4}-\d{2}-\d{2}$/;

function clampTargetDate(targetDate: string): string {
  if (!ISO.test(targetDate)) {
    throw new Error("ADP sync target date must be YYYY-MM-DD");
  }
  const today = chicagoTodayIso();
  const yesterday = shiftCalendarDate(today, "day", -1);
  return targetDate >= today ? yesterday : targetDate;
}

export type AdpSyncStart = {
  baselineScrapedAt: string | null;
  executionName?: string;
  targetDate: string;
  message: string;
};

/** Always a Cloud Run job — the ADP browser never runs on the operator's machine. */
export async function startAdpSync(
  store: string,
  targetDate: string,
): Promise<AdpSyncStart> {
  const date = clampTargetDate(targetDate);
  const baselineScrapedAt = await adpHoursScrapedAt();
  const { executionName } = await triggerAdpSync(store, date);
  return {
    baselineScrapedAt,
    executionName,
    targetDate: date,
    message:
      "ADP sync queued in the background — usually 5–12 min. You can keep using the page.",
  };
}

/** A sync already running on Cloud Run (e.g. started before a page reload), with a baseline its hours must beat. */
export async function runningAdpSync(): Promise<{ executionName: string; baselineScrapedAt: string } | null> {
  const hit = await runningAdpSyncExecution();
  // Same "YYYY-MM-DD HH:MM:SS" shape as the BQ scraped_at string, so they compare lexically.
  return hit ? { executionName: hit.name, baselineScrapedAt: hit.createTime.replace("T", " ").slice(0, 19) } : null;
}

export type AdpSyncPoll = {
  scrapedAt: string | null;
  advanced: boolean;
  execution?: CloudRunExecutionStatus;
};

export async function pollAdpSync(opts: {
  baselineScrapedAt: string | null;
  executionName?: string | null;
}): Promise<AdpSyncPoll> {
  const scrapedAt = await adpHoursScrapedAt();
  const baseline = opts.baselineScrapedAt ?? "";
  const advanced = Boolean(scrapedAt && scrapedAt > baseline);
  let execution: CloudRunExecutionStatus | undefined;
  if (opts.executionName) {
    execution = await getCloudRunExecutionStatus(opts.executionName);
  }
  return { scrapedAt, advanced, execution };
}
