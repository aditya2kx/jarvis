/** Square `square_transactions.source` multi-select + chart breakdown URL params. */

/** URL token for "no sources selected" (distinct from omit/All). */
export const SOURCES_NONE = "__none__";

function firstValue(v: string | string[] | undefined): string | undefined {
  if (Array.isArray(v)) return v[0];
  return v;
}

/**
 * Parse `sources` search param.
 * - `null` — all sources (param missing / "All")
 * - `[]` — none selected (`sources=__none__`) so Clear can uncheck everything
 * - `string[]` — filtered set
 */
export function parseSources(value: string | string[] | undefined): string[] | null {
  const raw = firstValue(value)?.trim();
  if (raw === undefined || raw === "" || raw === "All") return null;
  if (raw === SOURCES_NONE) return [];
  const parts = raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  if (parts.length === 0) return [];
  return Array.from(new Set(parts)).sort((a, b) => a.localeCompare(b));
}

/**
 * Serialize for the URL.
 * - all (`null`) → `""` (caller omits the param)
 * - none (`[]`) → `__none__`
 * - partial → comma-joined names
 */
export function serializeSources(sources: string[] | null): string {
  if (sources == null) return "";
  if (sources.length === 0) return SOURCES_NONE;
  return sources.join(",");
}

/** `breakdown=1` → true; missing / other → false (aggregate). */
export function parseBreakdown(value: string | string[] | undefined): boolean {
  const raw = firstValue(value)?.trim();
  return raw === "1" || raw === "true";
}

/**
 * Normalize a multi-select choice against the known option list.
 * - empty → none (`[]`) — Clear unchecks everything so the operator can pick a few
 * - every option → all (`null`) — Select All collapses to the default full-store view
 * - otherwise → sorted unique partial list
 */
export function normalizeSourceSelection(
  selected: string[],
  options: string[],
): string[] | null {
  if (selected.length === 0) return [];
  if (options.length > 0 && selected.length >= options.length) {
    const optSet = new Set(options);
    if (selected.every((s) => optSet.has(s)) && options.every((o) => selected.includes(o))) {
      return null;
    }
  }
  return Array.from(new Set(selected)).sort((a, b) => a.localeCompare(b));
}
