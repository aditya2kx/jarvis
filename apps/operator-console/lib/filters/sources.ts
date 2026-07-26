/** Square `square_transactions.source` multi-select + chart breakdown URL params. */

function firstValue(v: string | string[] | undefined): string | undefined {
  if (Array.isArray(v)) return v[0];
  return v;
}

/**
 * Parse `sources` search param. `null` means all sources (no filter).
 * Empty / missing / "All" → null. Comma-separated list → selected set (sorted unique).
 */
export function parseSources(value: string | string[] | undefined): string[] | null {
  const raw = firstValue(value)?.trim();
  if (!raw || raw === "All") return null;
  const parts = raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  if (parts.length === 0) return null;
  return Array.from(new Set(parts)).sort((a, b) => a.localeCompare(b));
}

/** Serialize selected sources for the URL. Empty string when all (caller omits param). */
export function serializeSources(sources: string[] | null): string {
  if (sources == null || sources.length === 0) return "";
  return sources.join(",");
}

/** `breakdown=1` → true; missing / other → false (aggregate). */
export function parseBreakdown(value: string | string[] | undefined): boolean {
  const raw = firstValue(value)?.trim();
  return raw === "1" || raw === "true";
}

/**
 * Normalize a multi-select choice against the known option list.
 * Selecting none or every option collapses to "all" (null) so the URL stays clean
 * and unfiltered totals match the model rollup.
 */
export function normalizeSourceSelection(
  selected: string[],
  options: string[],
): string[] | null {
  if (selected.length === 0) return null;
  if (options.length > 0 && selected.length >= options.length) {
    const optSet = new Set(options);
    if (selected.every((s) => optSet.has(s)) && options.every((o) => selected.includes(o))) {
      return null;
    }
  }
  return Array.from(new Set(selected)).sort((a, b) => a.localeCompare(b));
}
