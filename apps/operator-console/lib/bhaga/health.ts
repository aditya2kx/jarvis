import "server-only";
import { GoogleAuth } from "google-auth-library";
import { q, fq } from "@/lib/bq/client";

const PROJECT = process.env.BQ_PROJECT ?? "jarvis-bhaga-prod";
const FIRESTORE_DB = process.env.BHAGA_FIRESTORE_DB ?? "(default)";
const FIRESTORE_COLLECTION = process.env.BHAGA_FIRESTORE_COLLECTION ?? "runs";
const HALT_DOC = "_pipeline_state";

/** Age past which the data window is called stale, in days. */
export const STALE_AFTER_DAYS = 2;

export type SystemHealth = {
  halted: boolean;
  haltReason: string | null;
  haltScope: "model" | "all" | null;
  haltSince: string | null;
  /** Latest date present in the model, ISO. Null when the model is empty. */
  dataWindowEnd: string | null;
  /** Whole days between dataWindowEnd and today (America/Chicago). */
  dataAgeDays: number | null;
  stale: boolean;
  /** Set when health could not be determined; the banner says so rather than lying. */
  error: string | null;
};

/** Firestore REST value union, narrowed to the fields the halt doc uses. */
type FsValue = { stringValue?: string; booleanValue?: boolean; nullValue?: null };

function fsString(fields: Record<string, FsValue>, key: string): string | null {
  return fields[key]?.stringValue ?? null;
}

async function accessToken(label: string): Promise<string> {
  const auth = new GoogleAuth({
    scopes: ["https://www.googleapis.com/auth/cloud-platform"],
  });
  const client = await auth.getClient();
  const token = await client.getAccessToken();
  if (!token.token) throw new Error(`${label}: failed to obtain ADC access token`);
  return token.token;
}

type HaltState = Pick<
  SystemHealth,
  "halted" | "haltReason" | "haltScope" | "haltSince"
>;

const NOT_HALTED: HaltState = {
  halted: false,
  haltReason: null,
  haltScope: null,
  haltSince: null,
};

/**
 * Read the pipeline breaker from Firestore over REST.
 *
 * The console has no Firestore SDK; it already authenticates Cloud Run REST with
 * ADC, so this reuses that rather than taking on a new dependency. The document
 * shape is written by skills/bhaga_config/state_adapter.py.
 *
 * An expired halt reads as not-halted, matching get_pipeline_halt() — the banner
 * must agree with what the nightly will actually do.
 */
export async function readPipelineHalt(): Promise<HaltState> {
  const token = await accessToken("readPipelineHalt");
  const url =
    `https://firestore.googleapis.com/v1/projects/${PROJECT}/databases/` +
    `${encodeURIComponent(FIRESTORE_DB)}/documents/${FIRESTORE_COLLECTION}/${HALT_DOC}`;
  const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
  if (res.status === 404) return NOT_HALTED; // never tripped on this deployment
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`readPipelineHalt: HTTP ${res.status} ${text.slice(0, 300)}`);
  }
  const json = (await res.json()) as { fields?: Record<string, FsValue> };
  const fields = json.fields ?? {};
  if (!fields.halted?.booleanValue) return NOT_HALTED;

  const expiresAt = fsString(fields, "expires_at");
  if (expiresAt && Date.parse(expiresAt) <= Date.now()) return NOT_HALTED;

  const scope = fsString(fields, "scope");
  return {
    halted: true,
    haltReason: fsString(fields, "reason"),
    haltScope: scope === "all" ? "all" : "model",
    haltSince: fsString(fields, "since"),
  };
}

/** Latest date the model actually covers. */
export async function readDataWindowEnd(): Promise<string | null> {
  const rows = await q<{ data_window_end: string | null }>(
    `SELECT CAST(MAX(date) AS STRING) AS data_window_end FROM ${fq("model_daily")}`,
  );
  return rows[0]?.data_window_end ?? null;
}

/** Whole days between an ISO date and today in America/Chicago. */
export function ageInDays(dateIso: string, now: Date = new Date()): number {
  const today = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Chicago",
  }).format(now);
  const ms = Date.parse(`${today}T00:00:00Z`) - Date.parse(`${dateIso}T00:00:00Z`);
  return Math.round(ms / 86_400_000);
}

/**
 * Server-derived health: breaker state plus how old the data is.
 *
 * Derived on the server so every viewer sees the same answer — this is a property
 * of the system, not of a session. The console showed six-day-old numbers as if
 * they were current (2026-09-07) precisely because no page asked this question.
 *
 * Never throws: a health check that fails closed would take down every page, so a
 * failure is reported through `error` and the banner says health is unknown.
 */
export async function getSystemHealth(): Promise<SystemHealth> {
  try {
    const [halt, dataWindowEnd] = await Promise.all([
      readPipelineHalt(),
      readDataWindowEnd(),
    ]);
    const dataAgeDays = dataWindowEnd ? ageInDays(dataWindowEnd) : null;
    return {
      ...halt,
      dataWindowEnd,
      dataAgeDays,
      stale: dataAgeDays !== null && dataAgeDays > STALE_AFTER_DAYS,
      error: null,
    };
  } catch (e) {
    return {
      ...NOT_HALTED,
      dataWindowEnd: null,
      dataAgeDays: null,
      stale: false,
      error: e instanceof Error ? e.message : String(e),
    };
  }
}
