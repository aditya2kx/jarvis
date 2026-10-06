// Single source of truth for the internal store key -> human display name
// (Figma shows "Austin", BQ/config use the "palmetto" key everywhere —
// see docs/operator-console/PLAN.md). Add Houston here when it launches
// Sept 2026; never hardcode the display string at a call site.
export const STORE_DISPLAY: Record<string, string> = {
  palmetto: "Austin",
};

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
