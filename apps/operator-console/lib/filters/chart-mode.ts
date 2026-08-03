/**
 * Composition | Trend mode + Compare URL params.
 * Shared across Performance screens (Sales first; Labor/Forecast/etc. reuse).
 * Pair with `lib/charts/compare-series.ts` + `priorWindow` in `lib/filters/range.ts`.
 */

import type { Grain } from "@/lib/filters/range";

export type ChartMode = "composition" | "trend";

/** Trend Compare lag — independent of Aggregation (Issue #202 follow-on). */
export type CompareMode = "off" | "day" | "week" | "month";

export const COMPARE_OPTIONS: { value: CompareMode; label: string }[] = [
  { value: "off", label: "Off" },
  { value: "day", label: "Previous day" },
  { value: "week", label: "Previous week" },
  { value: "month", label: "Previous month" },
];

function firstValue(v: string | string[] | undefined): string | undefined {
  if (Array.isArray(v)) return v[0];
  return v;
}

/** `mode=trend` → trend; missing / other → composition. */
export function parseChartMode(value: string | string[] | undefined): ChartMode {
  const raw = firstValue(value)?.trim().toLowerCase();
  return raw === "trend" ? "trend" : "composition";
}

/**
 * Parse Compare URL param.
 * - `off` / missing / `0` / `false` → off
 * - `day` | `week` | `month` → that lag
 * - Legacy `1` / `true` → lag-1 matching Aggregation (`grain`)
 */
export function parseCompare(
  value: string | string[] | undefined,
  grain: Grain = "day",
): CompareMode {
  const raw = firstValue(value)?.trim().toLowerCase();
  if (!raw || raw === "0" || raw === "false" || raw === "off") return "off";
  if (raw === "day" || raw === "week" || raw === "month") return raw;
  if (raw === "1" || raw === "true") {
    // Legacy lag-1 matched Aggregation; weekday Aggregation falls back to week.
    if (grain === "weekday") return "week";
    if (grain === "day" || grain === "week" || grain === "month") return grain;
  }
  return "off";
}

/**
 * Enforce mutual exclusion: Breakdown only in Composition; Compare only in Trend.
 * Illegal URL combos are coerced (never both breakdown and compare-on).
 */
export function assertModeFilterCoherence(
  mode: ChartMode,
  breakdown: boolean,
  compare: CompareMode,
): { mode: ChartMode; breakdown: boolean; compare: CompareMode } {
  if (mode === "composition") {
    return { mode, breakdown, compare: "off" };
  }
  return { mode, breakdown: false, compare };
}
