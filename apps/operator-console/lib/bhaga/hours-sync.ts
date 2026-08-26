import "server-only";
import { spawn } from "node:child_process";
import path from "node:path";
import { adpHoursScrapedAt } from "@/lib/bq/queries";
import {
  getCloudRunExecutionStatus,
  triggerAdpTimecardSync,
  type CloudRunExecutionStatus,
} from "@/lib/bhaga/recompute";
import { chicagoTodayIso, shiftCalendarDate } from "@/lib/filters/range";

/** Monorepo root (apps/operator-console → ../..). */
function repoRoot(): string {
  return path.resolve(process.cwd(), "../..");
}

const ISO = /^\d{4}-\d{2}-\d{2}$/;

function clampTargetDate(targetDate: string): string {
  if (!ISO.test(targetDate)) {
    throw new Error("clocked hours target must be YYYY-MM-DD");
  }
  const today = chicagoTodayIso();
  const yesterday = shiftCalendarDate(today, "day", -1);
  return targetDate >= today ? yesterday : targetDate;
}

/**
 * Local console (BYPASS_IAP): Timecard-only refresh from this worktree.
 * Detached — caller polls scraped_at. Does not scrape pay_info.
 */
export function startLocalAdpTimecardSync(store: string, targetDate: string): void {
  const root = repoRoot();
  const date = clampTargetDate(targetDate);
  const child = spawn(
    process.env.PYTHON ?? "python3",
    [
      "-m",
      "agents.bhaga.scripts.daily_refresh",
      "--store",
      store,
      "--date",
      date,
      "--headless",
    ],
    {
      cwd: root,
      detached: true,
      stdio: "ignore",
      env: {
        ...process.env,
        BHAGA_ADP_TIMECARD_ONLY: "1",
        BHAGA_IGNORE_HALT: "1",
        BHAGA_DATASTORE: "bigquery",
        BHAGA_STORE: store,
        REFRESH_DATE: date,
        PYTHONUNBUFFERED: "1",
        BHAGA_ADP_HEADED: "",
      },
    },
  );
  child.unref();
}

export type HoursSyncStart = {
  mode: "local" | "cloud";
  baselineScrapedAt: string | null;
  executionName?: string;
  targetDate: string;
  message: string;
};

/** Prefer local scrape when BYPASS_IAP (dev laptop); else Cloud Run job. */
export async function startAdpTimecardSync(
  store: string,
  targetDate: string,
): Promise<HoursSyncStart> {
  const date = clampTargetDate(targetDate);
  const baselineScrapedAt = await adpHoursScrapedAt();
  const local = Boolean(process.env.BYPASS_IAP_EMAIL?.trim());

  if (local) {
    startLocalAdpTimecardSync(store, date);
    return {
      mode: "local",
      baselineScrapedAt,
      targetDate: date,
      message:
        "Syncing clocked hours in the background — usually 3–8 min. You can keep using the page.",
    };
  }

  const { executionName } = await triggerAdpTimecardSync(store, date);
  return {
    mode: "cloud",
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
