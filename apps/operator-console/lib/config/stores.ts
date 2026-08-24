// Single source of truth for the internal store key -> human display name
// (Figma shows "Austin", BQ/config use the "palmetto" key everywhere —
// see docs/operator-console/PLAN.md). Add Houston here when it launches
// Sept 2026; never hardcode the display string at a call site.
export const STORE_DISPLAY: Record<string, string> = {
  palmetto: "Austin",
};

/** ADP RUN tenant from agents/bhaga/knowledge-base/store-profiles/palmetto.json adp_run.tenant_uuid */
export const ADP_RUN_TENANT_UUID = "836d254c-789b-41b8-8052-d48a639e95d8";

export function storeDisplayName(store: string): string {
  return STORE_DISPLAY[store] ?? store;
}
