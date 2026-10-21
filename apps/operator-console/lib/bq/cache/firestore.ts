import "server-only";
import { GoogleAuth } from "google-auth-library";
import { PROJECT } from "@/lib/bq/bigquery";
import { isConsolePreview } from "@/lib/config/preview";
import { shared } from "./shared";

/**
 * The one shared cache layer (ARCHITECTURE.md §15): every console instance reads the same
 * table versions and the same cached results, so two people never see different numbers.
 *
 * - `console_cache_versions/{table}` `{v, updated_at, source}` — bumped by bhaga-webhook
 *   `/cache/bq-write` from BigQuery's audit log (every writer) and by this app's own writes.
 *   `__schema__` moves on any view / routine (re)definition.
 * - `console_query_cache/{ns}/console_query_cache_entries/{key}` `{payload, expire_at}` —
 *   TTL policy on `expire_at`. Previews use their own `ns`, so code under review never
 *   writes entries prod reads.
 */
const DB = process.env.BHAGA_FIRESTORE_DB ?? "(default)";
export const VERSIONS = "console_cache_versions";
export const SCHEMA_DOC = "__schema__";
const ENTRIES_ROOT = "console_query_cache";
const ENTRIES = "console_query_cache_entries";
/** Firestore caps a document at 1 MiB. */
export const MAX_PAYLOAD_CHARS = 900_000;
const ENTRY_TTL_MS = 6 * 3600_000;
const BACKOFF_MS = 60_000;
const FAILURES_BEFORE_BACKOFF = 5;

const st = shared("firestore", () => ({
  auth: null as GoogleAuth | null,
  disabledUntil: 0,
  consecutiveFailures: 0,
  lastError: null as string | null,
}));

export function namespace(): string {
  return isConsolePreview() ? (process.env.IAP_SERVICE_NAME ?? "preview") : "prod";
}

export function storeAvailable(now = Date.now()): boolean {
  return now >= st.disabledUntil;
}

export function storeState(): { namespace: string; available: boolean; lastError: string | null } {
  return { namespace: namespace(), available: storeAvailable(), lastError: st.lastError };
}

/** A failing store turns the cache off for a minute (reads go to BigQuery), never stale. */
function backOff(op: string, err: string): void {
  st.disabledUntil = Date.now() + BACKOFF_MS;
  st.consecutiveFailures = 0;
  st.lastError = `${op}: ${err.slice(0, 200)}`;
  console.error(`console_cache_store_unavailable op=${op} for_s=${BACKOFF_MS / 1000} err=${err.slice(0, 200)}`);
}

async function token(): Promise<string> {
  st.auth ??= new GoogleAuth({ scopes: ["https://www.googleapis.com/auth/datastore"] });
  const t = await (await st.auth.getClient()).getAccessToken();
  if (!t.token) throw new Error("no ADC access token");
  return t.token;
}

const BASE = () =>
  `https://firestore.googleapis.com/v1/projects/${PROJECT}/databases/${encodeURIComponent(DB)}/documents`;

function docName(path: string): string {
  return `projects/${PROJECT}/databases/${DB}/documents/${path}`;
}

async function call(op: string, url: string, init: RequestInit, timeoutMs: number): Promise<Response | null> {
  try {
    const res = await fetch(url, {
      ...init,
      headers: { Authorization: `Bearer ${await token()}`, "content-type": "application/json", ...init.headers },
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (res.ok || res.status === 404) {
      st.consecutiveFailures = 0;
      return res;
    }
    backOff(op, `HTTP ${res.status} ${await res.text()}`);
  } catch (e) {
    // One slow call (cold connection pool, a burst of parallel reads) only makes this read live;
    // a run of them means the store is unhealthy and every read should stop waiting on it.
    st.lastError = `${op}: ${String(e).slice(0, 200)}`;
    if (++st.consecutiveFailures >= FAILURES_BEFORE_BACKOFF) backOff(op, String(e));
  }
  return null;
}

type FsDoc = { name: string; fields?: Record<string, { integerValue?: string; stringValue?: string; timestampValue?: string }> };

/** All table versions (a table never written since the cache started is version 0). Null = store down. */
export async function readVersions(): Promise<Map<string, number> | null> {
  if (!storeAvailable()) return null;
  const out = new Map<string, number>();
  let pageToken = "";
  do {
    const url = `${BASE()}/${VERSIONS}?pageSize=1000&mask.fieldPaths=v${pageToken ? `&pageToken=${pageToken}` : ""}`;
    const res = await call("versions", url, { method: "GET" }, 1500);
    if (!res) return null;
    if (res.status === 404) return out;
    const body = (await res.json()) as { documents?: FsDoc[]; nextPageToken?: string };
    for (const d of body.documents ?? []) {
      out.set(d.name.slice(d.name.lastIndexOf("/") + 1), Number(d.fields?.v?.integerValue ?? 0));
    }
    pageToken = body.nextPageToken ? encodeURIComponent(body.nextPageToken) : "";
  } while (pageToken);
  return out;
}

/** This app wrote these tables: move their versions now, ahead of the audit-log event. */
export async function bumpVersions(tables: string[]): Promise<void> {
  if (tables.length === 0 || isConsolePreview()) return;
  const writes = tables.map((t) => ({
    update: { name: docName(`${VERSIONS}/${t}`), fields: { source: { stringValue: "console" } } },
    updateMask: { fieldPaths: ["source"] },
    updateTransforms: [
      { fieldPath: "v", increment: { integerValue: "1" } },
      { fieldPath: "updated_at", setToServerValue: "REQUEST_TIME" },
    ],
  }));
  await call("bump", `${BASE()}:commit`, { method: "POST", body: JSON.stringify({ writes }) }, 3000);
}

function entryUrl(key: string): string {
  return `${BASE()}/${ENTRIES_ROOT}/${encodeURIComponent(namespace())}/${ENTRIES}/${key}`;
}

export async function getEntry(key: string): Promise<string | null> {
  if (!storeAvailable()) return null;
  const res = await call("get", entryUrl(key), { method: "GET" }, 1000);
  if (!res || res.status === 404) return null;
  const doc = (await res.json()) as FsDoc;
  const exp = doc.fields?.expire_at?.timestampValue;
  if (exp && Date.parse(exp) <= Date.now()) return null;
  return doc.fields?.payload?.stringValue ?? null;
}

export async function putEntry(key: string, json: string): Promise<void> {
  if (!storeAvailable() || json.length > MAX_PAYLOAD_CHARS) return;
  await call(
    "put",
    entryUrl(key),
    {
      method: "PATCH",
      body: JSON.stringify({
        fields: {
          payload: { stringValue: json },
          expire_at: { timestampValue: new Date(Date.now() + ENTRY_TTL_MS).toISOString() },
        },
      }),
    },
    2000,
  );
}
