import "server-only";
import { GoogleAuth } from "google-auth-library";

const PROJECT = process.env.BQ_PROJECT ?? "jarvis-bhaga-prod";
const REGION = process.env.BHAGA_REGION ?? "us-central1";
const JOB = process.env.CLOUD_RUN_JOB_NAME_SHORT ?? "bhaga-daily-refresh";
const JOB_RESOURCE = `projects/${PROJECT}/locations/${REGION}/jobs/${JOB}`;

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

/**
 * Trigger bhaga-daily-refresh recompute-only for each distinct date.
 * Requires the operator-console runtime SA to hold run.developer on the job.
 */
export async function triggerModelRecompute(dates: string[]): Promise<void> {
  const unique = [...new Set(dates.filter(Boolean))].sort();
  if (!unique.length) return;

  const auth = new GoogleAuth({
    scopes: ["https://www.googleapis.com/auth/cloud-platform"],
  });
  const client = await auth.getClient();
  const token = await client.getAccessToken();
  if (!token.token) {
    throw new Error("triggerModelRecompute: failed to obtain ADC access token");
  }

  for (const date of unique) {
    const url = `https://run.googleapis.com/v2/${JOB_RESOURCE}:run`;
    const body = {
      overrides: {
        containerOverrides: [{ env: recomputeEnv(date) }],
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
      throw new Error(
        `triggerModelRecompute: Cloud Run job run failed for ${date}: ` +
          `HTTP ${res.status} ${text.slice(0, 400)}`,
      );
    }
  }
}
