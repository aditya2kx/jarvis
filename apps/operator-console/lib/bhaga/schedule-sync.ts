import "server-only";
import { spawn } from "node:child_process";
import path from "node:path";
import { adpScheduleScrapedAt } from "@/lib/bq/queries";
import {
  getCloudRunExecutionStatus,
  triggerAdpScheduleSync,
  type CloudRunExecutionStatus,
} from "@/lib/bhaga/recompute";

/** Monorepo root (apps/operator-console → ../..). */
function repoRoot(): string {
  return path.resolve(process.cwd(), "../..");
}

/**
 * Local console (BYPASS_IAP): run schedule-only refresh from this worktree so
 * BHAGA_ADP_SCHEDULE_ONLY is available before Cloud Run deploys this branch.
 * Detached — caller polls scraped_at.
 */
export function startLocalAdpScheduleSync(store: string): void {
  const root = repoRoot();
  const child = spawn(
    process.env.PYTHON ?? "python3",
    ["-m", "agents.bhaga.scripts.daily_refresh", "--store", store, "--headless"],
    {
      cwd: root,
      detached: true,
      stdio: "ignore",
      env: {
        ...process.env,
        BHAGA_ADP_SCHEDULE_ONLY: "1",
        BHAGA_IGNORE_HALT: "1",
        BHAGA_DATASTORE: "bigquery",
        BHAGA_STORE: store,
        PYTHONUNBUFFERED: "1",
        // Never open a visible Playwright window from the console button.
        // Interactive debug: BHAGA_ADP_SCHEDULE_ONLY=1 BHAGA_ADP_HEADED=1
        //   python -m agents.bhaga.scripts.daily_refresh --store palmetto
        BHAGA_ADP_HEADED: "",
      },
    },
  );
  child.unref();
}

export type ScheduleSyncStart = {
  mode: "local" | "cloud";
  baselineScrapedAt: string | null;
  executionName?: string;
  message: string;
};

/** Prefer local scrape when BYPASS_IAP (dev laptop); else Cloud Run job. */
export async function startAdpScheduleSync(store: string): Promise<ScheduleSyncStart> {
  const baselineScrapedAt = await adpScheduleScrapedAt();
  const local = Boolean(process.env.BYPASS_IAP_EMAIL?.trim());

  if (local) {
    startLocalAdpScheduleSync(store);
    return {
      mode: "local",
      baselineScrapedAt,
      message:
        "Syncing scheduled shifts in the background — usually 1–3 min. You can keep using the page.",
    };
  }

  const { executionName } = await triggerAdpScheduleSync(store);
  return {
    mode: "cloud",
    baselineScrapedAt,
    executionName,
    message:
      "Sync queued in the background — usually 1–3 min. You can keep using the page.",
  };
}

export type ScheduleSyncPoll = {
  scrapedAt: string | null;
  advanced: boolean;
  execution?: CloudRunExecutionStatus;
};

export async function pollAdpScheduleSync(opts: {
  baselineScrapedAt: string | null;
  executionName?: string | null;
}): Promise<ScheduleSyncPoll> {
  const scrapedAt = await adpScheduleScrapedAt();
  const baseline = opts.baselineScrapedAt ?? "";
  const advanced = Boolean(scrapedAt && scrapedAt > baseline);
  let execution: CloudRunExecutionStatus | undefined;
  if (opts.executionName) {
    execution = await getCloudRunExecutionStatus(opts.executionName);
  }
  return { scrapedAt, advanced, execution };
}
