import "server-only";
import { GoogleAuth } from "google-auth-library";
import { pickRecomputeAnchorDate } from "@/lib/bhaga/recomputeAnchor";

export { pickRecomputeAnchorDate } from "@/lib/bhaga/recomputeAnchor";

const PROJECT = process.env.BQ_PROJECT ?? "jarvis-bhaga-prod";
const REGION = process.env.BHAGA_REGION ?? "us-central1";
const JOB = process.env.CLOUD_RUN_JOB_NAME_SHORT ?? "bhaga-daily-refresh";
const JOB_RESOURCE = `projects/${PROJECT}/locations/${REGION}/jobs/${JOB}`;

async function runJob(env: { name: string; value: string }[], label: string): Promise<void> {
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
