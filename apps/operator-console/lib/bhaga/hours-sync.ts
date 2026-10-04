import "server-only";
import { adpHoursScrapedAt } from "@/lib/bq/queries";
import {
  getCloudRunExecutionStatus,
  triggerAdpTimecardSync,
  type CloudRunExecutionStatus,
} from "@/lib/bhaga/recompute";
import { chicagoTodayIso, shiftCalendarDate } from "@/lib/filters/range";

const ISO = /^\d{4}-\d{2}-\d{2}$/;

function clampTargetDate(targetDate: string): string {
  if (!ISO.test(targetDate)) {
    throw new Error("clocked hours target must be YYYY-MM-DD");
  }
  const today = chicagoTodayIso();
  const yesterday = shiftCalendarDate(today, "day", -1);
  return targetDate >= today ? yesterday : targetDate;
}

export type HoursSyncStart = {
  baselineScrapedAt: string | null;
  executionName?: string;
  targetDate: string;
  message: string;
};

/** Always a Cloud Run job — the ADP browser never runs on the operator's machine. */
export async function startAdpTimecardSync(
  store: string,
  targetDate: string,
): Promise<HoursSyncStart> {
  const date = clampTargetDate(targetDate);
  const baselineScrapedAt = await adpHoursScrapedAt();
  const { executionName } = await triggerAdpTimecardSync(store, date);
  return {
    baselineScrapedAt,
    executionName,
    targetDate: date,
    message:
      "Clocked-hours sync queued in the background — usually 3–8 min. You can keep using the page.",
  };
}

export type HoursSyncPoll = {
  scrapedAt: string | null;
  advanced: boolean;
  execution?: CloudRunExecutionStatus;
};

export async function pollAdpTimecardSync(opts: {
  baselineScrapedAt: string | null;
  executionName?: string | null;
}): Promise<HoursSyncPoll> {
  const scrapedAt = await adpHoursScrapedAt();
  const baseline = opts.baselineScrapedAt ?? "";
  const advanced = Boolean(scrapedAt && scrapedAt > baseline);
  let execution: CloudRunExecutionStatus | undefined;
  if (opts.executionName) {
    execution = await getCloudRunExecutionStatus(opts.executionName);
  }
  return { scrapedAt, advanced, execution };
}
