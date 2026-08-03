import "server-only";
import { GoogleAuth } from "google-auth-library";
import { pickRecomputeAnchorDate } from "@/lib/bhaga/recomputeAnchor";

export { pickRecomputeAnchorDate } from "@/lib/bhaga/recomputeAnchor";

const PROJECT = process.env.BQ_PROJECT ?? "jarvis-bhaga-prod";
const REGION = process.env.BHAGA_REGION ?? "us-central1";
const JOB = process.env.CLOUD_RUN_JOB_NAME_SHORT ?? "bhaga-daily-refresh";
const JOB_RESOURCE = `projects/${PROJECT}/locations/${REGION}/jobs/${JOB}`;

async function runJob(
  env: { name: string; value: string }[],
  label: string,
): Promise<{ executionName: string }> {
  const auth = new GoogleAuth({
    scopes: ["https://www.googleapis.com/auth/cloud-platform"],
  });
  const client = await auth.getClient();
  const token = await client.getAccessToken();
  if (!token.token) {
    throw new Error(`${label}: failed to obtain ADC access token`);
  }

  const url = `https://run.googleapis.com/v2/${JOB_RESOURCE}:run`;
  const body = {
    overrides: {
      containerOverrides: [{ env }],
    },
  };
  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token.token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${label}: Cloud Run job run failed: HTTP ${res.status} ${text.slice(0, 400)}`);
  }
  const json = (await res.json()) as {
    name?: string;
    metadata?: { name?: string; "@type"?: string };
  };
  // jobs.run returns an LRO; execution resource is in metadata.name.
  const executionName =
    (json.metadata?.name && json.metadata.name.includes("/executions/")
      ? json.metadata.name
      : null) ??
    (json.name?.includes("/executions/") ? json.name : null) ??
    "";
  if (!executionName) {
    // Fall back to operation name — caller may still poll scraped_at only.
    console.warn(
      `${label}: no execution name in run response; keys=${Object.keys(json).join(",")}`,
    );
  }
  return { executionName: executionName || json.name || "" };
}

/** Env overrides matching scripts/trigger_dated_refresh.py recompute-only mode. */
function recomputeEnv(date: string): { name: string; value: string }[] {
  return [
    { name: "REFRESH_DATE", value: date },
    { name: "BHAGA_SKIP_SQUARE", value: "1" },
    { name: "BHAGA_SKIP_ADP", value: "1" },
    { name: "BHAGA_SKIP_KDS", value: "1" },
    { name: "BHAGA_FORCE_MODEL_RECOMPUTE", value: "1" },
    { name: "BHAGA_IGNORE_HALT", value: "1" },
  ];
}

function orderRecoOnlyEnv(store: string): { name: string; value: string }[] {
  return [
    { name: "BHAGA_ORDER_RECO_ONLY", value: "1" },
    { name: "BHAGA_IGNORE_HALT", value: "1" },
    { name: "BHAGA_SKIP_SQUARE", value: "1" },
    { name: "BHAGA_SKIP_ADP", value: "1" },
    { name: "BHAGA_SKIP_KDS", value: "1" },
    // daily_refresh --store is CLI; job image reads STORE / default palmetto.
    // Pass both common knobs so the early-exit path has a store.
    { name: "BHAGA_STORE", value: store },
  ];
}

/**
 * Trigger one bhaga-daily-refresh recompute-only execution for a batch of dates.
 * Requires the operator-console runtime SA to hold run.developer on the job.
 */
export async function triggerModelRecompute(dates: string[]): Promise<string[]> {
  const anchor = pickRecomputeAnchorDate(dates);
  if (!anchor) return [];

  const touched = [...new Set(dates.filter(Boolean))].sort();
  await runJob(
    recomputeEnv(anchor),
    `triggerModelRecompute(anchor=${anchor}, touched=${touched.join(",")})`,
  );
  return touched;
}

/**
 * Enqueue dual-date order-reco refresh only (Issue #175 Option B).
 * Job short-circuits via BHAGA_ORDER_RECO_ONLY in daily_refresh.py.
 */
export async function triggerOrderRecoRefresh(store: string): Promise<void> {
  if (!store) throw new Error("triggerOrderRecoRefresh: store is required");
  await runJob(orderRecoOnlyEnv(store), `triggerOrderRecoRefresh(store=${store})`);
}

function adpScheduleOnlyEnv(store: string): { name: string; value: string }[] {
  return [
    { name: "BHAGA_ADP_SCHEDULE_ONLY", value: "1" },
    { name: "BHAGA_IGNORE_HALT", value: "1" },
    { name: "BHAGA_SKIP_SQUARE", value: "1" },
    { name: "BHAGA_SKIP_KDS", value: "1" },
    { name: "BHAGA_STORE", value: store },
  ];
}

/**
 * Enqueue ADP Team Schedule scrape + BQ upsert only (Issue #213).
 * Job short-circuits via BHAGA_ADP_SCHEDULE_ONLY in daily_refresh.py.
 * Returns the Cloud Run execution resource name for status polling.
 */
export async function triggerAdpScheduleSync(
  store: string,
): Promise<{ executionName: string }> {
  if (!store) throw new Error("triggerAdpScheduleSync: store is required");
  return runJob(adpScheduleOnlyEnv(store), `triggerAdpScheduleSync(store=${store})`);
}

export type CloudRunExecutionStatus = {
  done: boolean;
  succeeded: boolean | null;
  failed: boolean;
  message: string | null;
};

/** Poll a Cloud Run Job execution by full resource name. */
export async function getCloudRunExecutionStatus(
  executionName: string,
): Promise<CloudRunExecutionStatus> {
  if (!executionName || !executionName.includes("/executions/")) {
    return { done: false, succeeded: null, failed: false, message: null };
  }
  const auth = new GoogleAuth({
    scopes: ["https://www.googleapis.com/auth/cloud-platform"],
  });
  const client = await auth.getClient();
  const token = await client.getAccessToken();
  if (!token.token) {
    throw new Error("getCloudRunExecutionStatus: failed to obtain ADC access token");
  }
  const url = `https://run.googleapis.com/v2/${executionName}`;
  const res = await fetch(url, {
    headers: { Authorization: `Bearer ${token.token}` },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(
      `getCloudRunExecutionStatus: HTTP ${res.status} ${text.slice(0, 300)}`,
    );
  }
  const json = (await res.json()) as {
    completionTime?: string;
    conditions?: { type?: string; state?: string; message?: string }[];
    succeededCount?: number;
    failedCount?: number;
  };
  const failedCount = Number(json.failedCount ?? 0);
  const succeededCount = Number(json.succeededCount ?? 0);
  const cond = (json.conditions ?? []).find((c) => c.type === "Completed");
  const condFailed = (json.conditions ?? []).some(
    (c) => c.type === "Completed" && (c.state === "CONDITION_FAILED" || c.state === "False"),
  );
  const done = Boolean(json.completionTime) || succeededCount > 0 || failedCount > 0 || condFailed;
  const failed = failedCount > 0 || condFailed;
  const succeeded = done && !failed && succeededCount > 0;
  const message =
    cond?.message ??
    (failed ? "Cloud Run execution failed" : null);
  return { done, succeeded, failed, message };
}
