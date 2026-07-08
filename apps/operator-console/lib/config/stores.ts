// Single source of truth for the internal store key -> human display name
// (Figma shows "Austin", BQ/config use the "palmetto" key everywhere —
// see docs/operator-console/PLAN.md). Add Houston here when it launches
// Sept 2026; never hardcode the display string at a call site.
export const STORE_DISPLAY: Record<string, string> = {
  palmetto: "Austin",
};

export function storeDisplayName(store: string): string {
  return STORE_DISPLAY[store] ?? store;
}
