/**
 * Composition | Trend mode + Compare URL params.
 * Shared across Performance screens (Sales first; Labor/Forecast/etc. reuse).
 * Pair with `lib/charts/compare-series.ts` + `priorWindow` in `lib/filters/range.ts`.
 */

export type ChartMode = "composition" | "trend";

function firstValue(v: string | string[] | undefined): string | undefined {
  if (Array.isArray(v)) return v[0];
  return v;
}

/** `mode=trend` → trend; missing / other → composition. */
export function parseChartMode(value: string | string[] | undefined): ChartMode {
  const raw = firstValue(value)?.trim().toLowerCase();
  return raw === "trend" ? "trend" : "composition";
}

/** `compare=1` → true; missing / other → false. */
export function parseCompare(value: string | string[] | undefined): boolean {
  const raw = firstValue(value)?.trim();
  return raw === "1" || raw === "true";
}

/**
 * Enforce mutual exclusion: Breakdown only in Composition; Compare only in Trend.
 * Illegal URL combos are coerced (never both breakdown and compare).
 */
export function assertModeFilterCoherence(
  mode: ChartMode,
  breakdown: boolean,
  compare: boolean,
): { mode: ChartMode; breakdown: boolean; compare: boolean } {
  if (mode === "composition") {
    return { mode, breakdown, compare: false };
  }
  return { mode, breakdown: false, compare };
}
