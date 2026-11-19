import "server-only";
import { randomUUID } from "crypto";
import { intParam, mutate, q } from "@/lib/bq/client";
import { plaidFq } from "@/lib/plaid/tables";
import {
  isJobLive,
  jobProgressLabel,
  type CategorizeJob,
  type CategorizeJobKind,
} from "@/lib/plaid/categorize-job-status";
import { reapplyPlaidCategories, type CategorizeScope } from "@/lib/plaid/reapply-categories";

/**
 * Accounting categorize jobs (migration 098). The row is the shared truth for progress
 * across tabs / instances; the console polls GET /api/accounting/categorize-job.
 */
const JOB_COLUMNS = `job_id, kind, status, rule_id, total, done, updated, unchanged,
  skipped_override, message, started_by,
  CAST(started_at AS STRING) AS started_at, CAST(heartbeat_at AS STRING) AS heartbeat_at,
  CAST(finished_at AS STRING) AS finished_at`;

export async function latestCategorizeJob(): Promise<CategorizeJob | null> {
  const rows = await q<CategorizeJob>(
    `SELECT ${JOB_COLUMNS} FROM ${plaidFq("plaid_categorize_jobs")}
     ORDER BY started_at DESC LIMIT 1`,
  );
  return rows[0] ?? null;
}

/** The running job, if any is still heartbeating. */
export async function liveCategorizeJob(): Promise<CategorizeJob | null> {
  const rows = await q<CategorizeJob>(
    `SELECT ${JOB_COLUMNS} FROM ${plaidFq("plaid_categorize_jobs")}
     WHERE status = 'running'
       AND started_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 DAY)
     ORDER BY started_at ASC`,
  );
  return rows.find((j) => isJobLive(j)) ?? null;
}

/** Refusal text for a write that must not race a running job (null when none runs). */
export async function busyMessage(what: string): Promise<string | null> {
  const live = await liveCategorizeJob();
  if (!live) return null;
  return `${jobProgressLabel(live)}. ${what} is paused until it finishes — try again then. Nothing was saved.`;
}

async function insertJob(kind: CategorizeJobKind, by: string, ruleId: string | null): Promise<string> {
  const jobId = randomUUID();
  await mutate(
    `INSERT INTO ${plaidFq("plaid_categorize_jobs")}
       (job_id, kind, status, rule_id, started_by, started_at, heartbeat_at)
     VALUES (@job_id, @kind, 'running', @rule_id, @by, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())`,
    { job_id: jobId, kind, rule_id: ruleId, by },
    { rule_id: "STRING" },
  );
  return jobId;
}

async function patchJob(
  jobId: string,
  fields: Partial<Pick<CategorizeJob, "status" | "total" | "done" | "updated" | "unchanged" | "skipped_override" | "message">>,
  finished = false,
): Promise<void> {
  const sets: string[] = ["heartbeat_at = CURRENT_TIMESTAMP()"];
  const params: Record<string, unknown> = { job_id: jobId };
  const types: Record<string, string> = {};
  for (const [k, v] of Object.entries(fields)) {
    sets.push(`${k} = @${k}`);
    if (typeof v === "number") params[k] = intParam(v);
    else {
      params[k] = v ?? null;
      types[k] = "STRING";
    }
  }
  if (finished) sets.push("finished_at = CURRENT_TIMESTAMP()");
  await mutate(
    `UPDATE ${plaidFq("plaid_categorize_jobs")} SET ${sets.join(", ")} WHERE job_id = @job_id`,
    params,
    types,
  );
}

export type JobOutcome =
  | { started: true; job: CategorizeJob }
  | { started: false; job: CategorizeJob };

/**
 * Run a categorize job end to end, writing progress as it goes. When another job is
 * already live, returns it (`started: false`) instead of starting a second writer.
 */
export async function runCategorizeJob(opts: {
  kind: CategorizeJobKind;
  by: string;
  ruleId?: string | null;
  scope?: CategorizeScope;
  /** Runs inside the job before categorizing (e.g. disable the rule being reverted). */
  before?: () => Promise<void>;
}): Promise<JobOutcome> {
  const existing = await liveCategorizeJob();
  if (existing) return { started: false, job: existing };
  const jobId = await insertJob(opts.kind, opts.by, opts.ruleId ?? null);
  // Two tabs can pass the check above together; the earliest live job wins.
  const winner = await liveCategorizeJob();
  if (winner && winner.job_id !== jobId) {
    await patchJob(jobId, { status: "failed", message: `superseded by ${winner.job_id}` }, true);
    return { started: false, job: winner };
  }
  const t0 = Date.now();
  try {
    await opts.before?.();
    const result = await reapplyPlaidCategories(opts.scope, {
      onPlanned: (p) =>
        patchJob(jobId, {
          total: p.total,
          done: 0,
          unchanged: p.unchanged,
          skipped_override: p.skipped_override,
        }),
      onProgress: (done) => patchJob(jobId, { done }),
    });
    await patchJob(
      jobId,
      { status: "done", done: result.updated, updated: result.updated, unchanged: result.unchanged },
      true,
    );
    console.info(
      `plaid_categorize_job done job=${jobId} kind=${opts.kind} rule=${opts.ruleId ?? "-"} ` +
        `updated=${result.updated} unchanged=${result.unchanged} scanned=${result.scanned} ms=${Date.now() - t0}`,
    );
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    console.error(`plaid_categorize_job failed job=${jobId} kind=${opts.kind} ms=${Date.now() - t0}: ${msg}`);
    await patchJob(jobId, { status: "failed", message: msg.slice(0, 500) }, true).catch((pe) =>
      console.error(`plaid_categorize_job status write failed job=${jobId}: ${String(pe)}`),
    );
  }
  const rows = await q<CategorizeJob>(
    `SELECT ${JOB_COLUMNS} FROM ${plaidFq("plaid_categorize_jobs")} WHERE job_id = @job_id`,
    { job_id: jobId },
  );
  return { started: true, job: rows[0]! };
}
