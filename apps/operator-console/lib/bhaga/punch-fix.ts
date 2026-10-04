import "server-only";
import { punchGapDecisionStatuses } from "@/lib/bq/queries";
import {
  claimPunchGapDecisions,
  releasePunchGapDecisions,
} from "@/lib/bq/writes";
import {
  getCloudRunExecutionStatus,
  triggerPunchFixApply,
  type CloudRunExecutionStatus,
} from "@/lib/bhaga/recompute";
import { chicagoTodayIso, shiftCalendarDate } from "@/lib/filters/range";

export type PunchFixStart = {
  decisionIds: string[];
  executionName?: string;
  message: string;
};

/** Rough wall time: container start + login (~3 min), ~30 s per entry, resync (~2.5 min). */
export function punchFixMinutes(n: number): number {
  return Math.ceil(5.5 + n * 0.5);
}

export type PunchFixPoll = {
  statuses: { decisionId: string; status: string; error: string | null }[];
  done: boolean;
  execution?: CloudRunExecutionStatus;
};

/** Claim the decisions, then start the ADP write as a Cloud Run job — never in the operator's browser. */
export async function startPunchFixApply(
  store: string,
  ids: string[],
): Promise<PunchFixStart> {
  if (process.env.BYPASS_IAP_EMAIL?.trim() && !process.env.BHAGA_ADP_PREVIEW_JOB?.trim()) {
    // Local console runs unmerged code; the prod job's image cannot write punches yet.
    throw new Error("Set BHAGA_ADP_PREVIEW_JOB in .env.local (and restart the console) before writing to ADP.");
  }
  const claimed = await claimPunchGapDecisions(store, ids);
  if (!claimed.length) {
    throw new Error(
      "Nothing to write — these entries are already written or being written.",
    );
  }
  const date = shiftCalendarDate(chicagoTodayIso(), "day", -1);
  const n = `${claimed.length} punch fix${claimed.length === 1 ? "" : "es"}`;
  try {
    const { executionName } = await triggerPunchFixApply(store, claimed, date);
    return {
      decisionIds: claimed,
      executionName,
      message: `Writing ${n} to ADP on the server — about ${punchFixMinutes(claimed.length)} min. Safe to close this tab.`,
    };
  } catch (e) {
    await releasePunchGapDecisions(
      store,
      claimed,
      `could not start the write: ${String(e)}`,
    );
    throw e;
  }
}

export async function pollPunchFixApply(opts: {
  store: string;
  decisionIds: string[];
  executionName?: string | null;
}): Promise<PunchFixPoll> {
  const rows = await punchGapDecisionStatuses(opts.store, opts.decisionIds);
  const statuses = rows.map((r) => ({
    decisionId: r.decision_id,
    status: r.status,
    error: r.error,
  }));
  let execution: CloudRunExecutionStatus | undefined;
  if (opts.executionName)
    execution = await getCloudRunExecutionStatus(opts.executionName);
  const done =
    statuses.length > 0 && statuses.every((s) => s.status !== "applying");
  return { statuses, done, execution };
}
