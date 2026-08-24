import "server-only";
import { spawn } from "node:child_process";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import {
  getCloudRunExecutionStatus,
  triggerPayrollDraft,
  type CloudRunExecutionStatus,
} from "@/lib/bhaga/recompute";
import { payrollDraftRun } from "@/lib/bq/queries";

/** Same path as agents/bhaga/scripts/daily_refresh.py PAYROLL_DRAFT_STATUS_PATH. */
export const PAYROLL_DRAFT_STATUS_PATH =
  "/tmp/jarvis-adp-payroll-draft.status.json";

function repoRoot(): string {
  return path.resolve(process.cwd(), "../..");
}

export type PayrollDraftStart = {
  mode: "local" | "cloud";
  executionName?: string;
  message: string;
};

async function markLocalRunning(periodStart: string, periodEnd: string) {
  await writeFile(
    PAYROLL_DRAFT_STATUS_PATH,
    JSON.stringify({
      state: "running",
      period_start: periodStart,
      period_end: periodEnd,
    }) + "\n",
    "utf8",
  );
}

function startLocalPayrollDraft(
  store: string,
  periodStart: string,
  periodEnd: string,
): void {
  const root = repoRoot();
  const child = spawn(
    process.env.PYTHON ?? "python3",
    ["-m", "agents.bhaga.scripts.daily_refresh", "--store", store, "--no-slack"],
    {
      cwd: root,
      detached: true,
      stdio: "ignore",
      env: {
        ...process.env,
        BHAGA_PAYROLL_DRAFT_ONLY: "1",
        BHAGA_ADP_PAYROLL_DRAFT: "1",
        BHAGA_IGNORE_HALT: "1",
        BHAGA_DATASTORE: "bigquery",
        BHAGA_STORE: store,
        BHAGA_PAYROLL_PERIOD_START: periodStart,
        BHAGA_PAYROLL_PERIOD_END: periodEnd,
        PYTHONUNBUFFERED: "1",
        BHAGA_HEADLESS: "1",
        BHAGA_ADP_HEADED: "",
      },
    },
  );
  child.unref();
}

/** Local BYPASS_IAP: run this worktree. Else Cloud Run. */
export async function startPayrollDraft(
  store: string,
  periodStart: string,
  periodEnd: string,
): Promise<PayrollDraftStart> {
  const local = Boolean(process.env.BYPASS_IAP_EMAIL?.trim());
  if (local) {
    await markLocalRunning(periodStart, periodEnd);
    startLocalPayrollDraft(store, periodStart, periodEnd);
    return {
      mode: "local",
      message: "Processing ADP Preview — no browser window. You submit in ADP.",
    };
  }
  const { executionName } = await triggerPayrollDraft(
    store,
    periodStart,
    periodEnd,
  );
  return {
    mode: "cloud",
    executionName,
    message:
      "ADP Preview queued (headless). Hours and total pay vs last Preview will show when it finishes.",
  };
}

export type PayrollDraftPoll = CloudRunExecutionStatus & {
  mode?: "local" | "cloud";
  previewHours?: number | null;
  previewGross?: number | null;
};

function numFromUnknown(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function snapshotFromRun(run: {
  preview_hours?: number | null;
  preview_gross?: number | null;
} | null): { previewHours: number | null; previewGross: number | null } {
  return {
    previewHours: numFromUnknown(run?.preview_hours),
    previewGross: numFromUnknown(run?.preview_gross),
  };
}

export async function pollPayrollDraft(opts: {
  executionName?: string | null;
  mode?: "local" | "cloud" | null;
  periodStart: string;
  periodEnd: string;
  store?: string;
}): Promise<PayrollDraftPoll> {
  const store = opts.store?.trim() || DEFAULT_STORE;
  const bqRun = await payrollDraftRun(store, opts.periodStart, opts.periodEnd);
  const snap = snapshotFromRun(bqRun);

  if (opts.mode === "local" || !opts.executionName) {
    try {
      const raw = await readFile(PAYROLL_DRAFT_STATUS_PATH, "utf8");
      const json = JSON.parse(raw) as {
        state?: string;
        error?: string;
        period_start?: string;
        period_end?: string;
        preview_hours?: unknown;
        preview_gross?: unknown;
      };
      const fileSnap = {
        previewHours:
          numFromUnknown(json.preview_hours) ?? snap.previewHours,
        previewGross:
          numFromUnknown(json.preview_gross) ?? snap.previewGross,
      };
      const samePeriod =
        !json.period_start ||
        (json.period_start === opts.periodStart &&
          json.period_end === opts.periodEnd);
      if (json.state === "running" && samePeriod) {
        return {
          done: false,
          succeeded: null,
          failed: false,
          message: null,
          mode: "local",
          ...fileSnap,
        };
      }
      if (json.state === "fail" && samePeriod) {
        return {
          done: true,
          succeeded: false,
          failed: true,
          message: json.error ?? "Local ADP Preview failed",
          mode: "local",
          ...fileSnap,
        };
      }
      if (bqRun?.status === "fail") {
        return {
          done: true,
          succeeded: false,
          failed: true,
          message: bqRun.error ?? "ADP Preview failed",
          mode: "local",
          ...snap,
        };
      }
      if ((json.state === "ok" && samePeriod) || bqRun?.status === "ok") {
        return {
          done: true,
          succeeded: true,
          failed: false,
          message: null,
          mode: "local",
          ...fileSnap,
        };
      }
      return {
        done: false,
        succeeded: null,
        failed: false,
        message: null,
        mode: "local",
        ...fileSnap,
      };
    } catch {
      if (bqRun?.status === "ok") {
        return {
          done: true,
          succeeded: true,
          failed: false,
          message: null,
          mode: "local",
          ...snap,
        };
      }
      if (bqRun?.status === "fail") {
        return {
          done: true,
          succeeded: false,
          failed: true,
          message: bqRun.error ?? "ADP Preview failed",
          mode: "local",
          ...snap,
        };
      }
      return {
        done: false,
        succeeded: null,
        failed: false,
        message: null,
        mode: "local",
        ...snap,
      };
    }
  }

  const status = await getCloudRunExecutionStatus(opts.executionName);
  if (status.failed) {
    return { ...status, mode: "cloud", ...snap };
  }
  if (bqRun?.status === "ok") {
    return {
      done: true,
      succeeded: true,
      failed: false,
      message: null,
      mode: "cloud",
      ...snap,
    };
  }
  if (bqRun?.status === "fail" && status.done) {
    return {
      done: true,
      succeeded: false,
      failed: true,
      message: bqRun.error ?? status.message ?? "ADP Preview failed",
      mode: "cloud",
      ...snap,
    };
  }
  return { ...status, mode: "cloud", ...snap };
}
