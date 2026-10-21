import "server-only";
import { GoogleAuth } from "google-auth-library";
import { PROJECT } from "@/lib/bq/bigquery";
import { isConsolePreview } from "@/lib/config/preview";

/**
 * Shared L2 in Firestore, so a fresh instance (deploy, scale-out) starts warm. Same keys
 * as L1 — an entry is only ever addressed by the table versions it was computed from, so
 * sharing it across instances cannot serve a stale number.
 *
 * Opt-in (`CONSOLE_QUERY_CACHE_L2=1`) because it needs a one-time Firestore TTL policy on
 * `expire_at` and `roles/datastore.user` for the runtime SA (RUNBOOK.md §17). Never in
 * read-only previews: they must not write to prod stores.
 */
const DB = process.env.BHAGA_FIRESTORE_DB ?? "(default)";
const COLLECTION = "console_query_cache";
/** Firestore caps a document at 1 MiB. */
const MAX_PAYLOAD_CHARS = 900_000;
const TIMEOUT_MS = 800;
const TTL_MS = 6 * 3600_000;
const BACKOFF_MS = 10 * 60_000;

let auth: GoogleAuth | null = null;
let disabledUntil = 0;
let lastError: string | null = null;

export function l2Enabled(now = Date.now()): boolean {
  return process.env.CONSOLE_QUERY_CACHE_L2 === "1" && !isConsolePreview() && now >= disabledUntil;
}

export function l2State(): { configured: boolean; enabled: boolean; lastError: string | null } {
  return { configured: process.env.CONSOLE_QUERY_CACHE_L2 === "1", enabled: l2Enabled(), lastError };
}

function backOff(op: string, err: string): void {
  disabledUntil = Date.now() + BACKOFF_MS;
  lastError = `${op}: ${err.slice(0, 200)}`;
  console.error(`console_cache_l2_disabled op=${op} for_s=${BACKOFF_MS / 1000} err=${err.slice(0, 200)}`);
}

async function token(): Promise<string> {
  auth ??= new GoogleAuth({ scopes: ["https://www.googleapis.com/auth/datastore"] });
  const t = await (await auth.getClient()).getAccessToken();
  if (!t.token) throw new Error("no ADC access token");
  return t.token;
}

function docUrl(key: string): string {
  return (
    `https://firestore.googleapis.com/v1/projects/${PROJECT}/databases/` +
    `${encodeURIComponent(DB)}/documents/${COLLECTION}/${key}`
  );
}

export async function l2Get(key: string): Promise<string | null> {
  if (!l2Enabled()) return null;
  try {
    const res = await fetch(docUrl(key), {
      headers: { Authorization: `Bearer ${await token()}` },
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (res.status === 404) return null;
    if (!res.ok) {
      backOff("get", `HTTP ${res.status} ${await res.text()}`);
      return null;
    }
    const doc = (await res.json()) as {
      fields?: { payload?: { stringValue?: string }; expire_at?: { timestampValue?: string } };
    };
    const exp = doc.fields?.expire_at?.timestampValue;
    if (exp && Date.parse(exp) <= Date.now()) return null;
    return doc.fields?.payload?.stringValue ?? null;
  } catch (e) {
    backOff("get", String(e));
    return null;
  }
}

export async function l2Put(key: string, json: string): Promise<void> {
  if (!l2Enabled() || json.length > MAX_PAYLOAD_CHARS) return;
  try {
    const res = await fetch(docUrl(key), {
      method: "PATCH",
      headers: { Authorization: `Bearer ${await token()}`, "content-type": "application/json" },
      body: JSON.stringify({
        fields: {
          payload: { stringValue: json },
          expire_at: { timestampValue: new Date(Date.now() + TTL_MS).toISOString() },
        },
      }),
      signal: AbortSignal.timeout(TIMEOUT_MS * 2),
    });
    if (!res.ok) backOff("put", `HTTP ${res.status} ${await res.text()}`);
  } catch (e) {
    backOff("put", String(e));
  }
}
