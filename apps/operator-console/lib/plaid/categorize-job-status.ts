/** Pure job-status helpers shared by the server (guards) and the ledger UI (progress). */

export type CategorizeJobKind = "reapply" | "revert_rule";
export type CategorizeJobState = "running" | "done" | "failed";

export interface CategorizeJob {
  job_id: string;
  kind: CategorizeJobKind;
  status: CategorizeJobState;
  rule_id: string | null;
  total: number | null;
  done: number | null;
  updated: number | null;
  unchanged: number | null;
  skipped_override: number | null;
  message: string | null;
  started_by: string | null;
  started_at: string;
  heartbeat_at: string | null;
  finished_at: string | null;
}

/** A running job whose heartbeat is older than this is treated as dead (instance restart). */
export const JOB_STALE_MS = 3 * 60_000;

/** Parses BigQuery's CAST(TIMESTAMP AS STRING) form `2026-11-19 11:59:55.123456+00` and ISO. */
export function parseBqTimestamp(v: string | null | undefined): number {
  if (!v) return NaN;
  const iso = v
    .trim()
    .replace(" ", "T")
    .replace(/(\.\d{3})\d+/, "$1")
    .replace(/([+-]\d{2})$/, "$1:00");
  return Date.parse(iso);
}

const ts = parseBqTimestamp;

export function isJobLive(job: CategorizeJob | null | undefined, now = Date.now()): boolean {
  if (!job || job.status !== "running") return false;
  const beat = ts(job.heartbeat_at) || ts(job.started_at);
  return Number.isFinite(beat) && now - beat < JOB_STALE_MS;
}

/** A running row whose heartbeat stopped: the instance died mid-job. */
export function isJobStalled(job: CategorizeJob | null | undefined, now = Date.now()): boolean {
  return Boolean(job && job.status === "running" && !isJobLive(job, now));
}

const n = (v: number | null | undefined) => Number(v ?? 0).toLocaleString("en-US");

function kindLabel(kind: CategorizeJobKind): string {
  return kind === "revert_rule" ? "Reverting rule" : "Reapplying rules";
}

/** One line for the ledger header / tray, e.g. "Reapplying rules — 400 of 1,012 rows written". */
export function jobProgressLabel(job: CategorizeJob, now = Date.now()): string {
  if (isJobStalled(job, now)) {
    return `${kindLabel(job.kind)} stopped at ${n(job.done)} of ${n(job.total)} rows — the server restarted. Run Reapply again; nothing is lost.`;
  }
  if (job.status === "running") {
    if (job.total == null) return `${kindLabel(job.kind)} — checking every transaction against the rules…`;
    if (!job.total) return `${kindLabel(job.kind)} — nothing to change, finishing…`;
    return `${kindLabel(job.kind)} — ${n(job.done)} of ${n(job.total)} rows written`;
  }
  if (job.status === "failed") return `${kindLabel(job.kind)} failed: ${job.message || "unknown error"}`;
  return job.kind === "revert_rule"
    ? `Rule ${job.rule_id ?? ""} reverted — ${n(job.updated)} transaction(s) re-categorized`
    : `Rules reapplied — ${n(job.updated)} changed, ${n(job.unchanged)} unchanged (${n(job.skipped_override)} overrides kept)`;
}

/** 0–100 for a progress bar; null while the total is still unknown. */
export function jobPercent(job: CategorizeJob): number | null {
  if (job.status === "done") return 100;
  if (job.total == null) return null;
  if (!job.total) return 100;
  return Math.max(0, Math.min(100, Math.round((Number(job.done ?? 0) / job.total) * 100)));
}
