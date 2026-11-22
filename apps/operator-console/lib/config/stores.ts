// Single source of truth for the stores the console serves. `palmetto` is the
// historical internal key for Austin (BQ/config use it everywhere — see
// docs/operator-console/PLAN.md); never hardcode a display string at a call site.
export const STORE_IDS = ["palmetto", "houston"] as const;
export type StoreId = (typeof STORE_IDS)[number];

export const DEFAULT_STORE_ID: StoreId = "palmetto";

export const STORE_DISPLAY: Record<string, string> = {
  palmetto: "Austin",
  houston: "Houston",
};

/** Firestore collection holding each store's pipeline run state (halt breaker, OTP). */
export const STORE_FIRESTORE_COLLECTION: Record<StoreId, string> = {
  palmetto: process.env.BHAGA_FIRESTORE_COLLECTION ?? "runs",
  houston: "runs_houston",
};

/** GCS scrape-cache bucket per store (store profile `gcs_cache_bucket`). */
export const STORE_GCS_CACHE_BUCKET: Record<StoreId, string> = {
  palmetto: "bhaga-scrape-cache",
  houston: "bhaga-scrape-cache-houston",
};

/** Which store the operator is looking at; shared by every page like the Period cookie. */
export const STORE_COOKIE = "oc_store";

/** Anything that is not a known store id resolves to the default store, never to an error. */
export function parseStore(raw: unknown): StoreId {
  return (STORE_IDS as readonly string[]).includes(String(raw)) ? (raw as StoreId) : DEFAULT_STORE_ID;
}

/** Browser-side read of the store cookie (null on the server or when unset). */
export function readStoreCookie(): StoreId | null {
  if (typeof document === "undefined") return null;
  const m = document.cookie.match(new RegExp(`(?:^|;\\s*)${STORE_COOKIE}=([^;]*)`));
  return m ? parseStore(decodeURIComponent(m[1])) : null;
}

/** ADP RUN tenant from agents/bhaga/knowledge-base/store-profiles/palmetto.json adp_run.tenant_uuid */
export const ADP_RUN_TENANT_UUID = "836d254c-789b-41b8-8052-d48a639e95d8";

/**
 * Monthly recognition sources (Issue #369) — mirrors palmetto.json clickup.*_channel
 * and square.location_id. Gift cards are issued at the store's Square location.
 */
export const RECOGNITION: Record<
  string,
  {
    recognitionChannelId: string;
    runningChannelId: string;
    coverageChannelId: string;
    squareLocationId: string;
    /** Draft + recap DMs go to this ClickUp user (same default as Team pulse). */
    operatorClickupUserId: string;
    /** "Reach out to …" in the post when gift cards are not emailed. */
    leadClickupUserId: string;
  }
> = {
  palmetto: {
    recognitionChannelId: "8cr6661-2837",
    runningChannelId: "8cr6661-737",
    coverageChannelId: "8cr6661-1617",
    squareLocationId: "L9DDZF3CQS8AK",
    operatorClickupUserId: "198109189",
    leadClickupUserId: "95364402",
  },
};

export function storeDisplayName(store: string): string {
  return STORE_DISPLAY[store] ?? store;
}
