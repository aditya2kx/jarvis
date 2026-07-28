/**
 * Shared Trend “Compare” helpers — used by Sales today; Labor / Forecast / etc.
 * should reuse these (not copy) when they add Composition | Trend modes.
 *
 * Contract:
 * - `priorWindow(win, displayGrain, compareGrain)` in `lib/filters/range.ts`
 * - `mergePriorSeries` overlays prior abs on left axis + `% change` on right
 * - `LineChartCard` renders dual Y-axis when any series has `yAxisId: "right"`
 * - Mode gating (`composition` vs `trend`) lives in `lib/filters/chart-mode.ts`
 */
import type { Series } from "@/components/charts/LineChartCard";
import type { Grain } from "@/lib/filters/range";
import type { CompareMode } from "@/lib/filters/chart-mode";

/** Operator-facing Compare dropdown / series label for the selected lag. */
export function compareGrainLabel(compare: Grain | CompareMode): string {
  if (compare === "off") return "Off";
  if (compare === "week") return "Previous week";
  if (compare === "month") return "Previous month";
  return "Previous day";
}

/**
 * Percent change current vs prior. Null when either side is missing or prior is 0
 * (avoid ±Infinity on a zero baseline).
 */
export function pctChange(
  current: number | null | undefined,
  prior: number | null | undefined,
): number | null {
  if (current == null || prior == null) return null;
  if (Number.isNaN(current) || Number.isNaN(prior)) return null;
  if (prior === 0) return null;
  return ((current - prior) / Math.abs(prior)) * 100;
}

export type PivotChart = {
  data: Record<string, unknown>[];
  series: Series[];
};

/**
 * Align prior aggregate pivot onto current bucket labels by index (after
 * `priorWindow` + spine fill). Adds:
 * - `prior_<metricKey>` — dashed, left Y-axis (absolute)
 * - `pct_<metricKey>` — solid, right Y-axis (% change)
 * - `prior_bucket` — tooltip label for the paired prior grain
 */
export function mergePriorSeries(
  current: PivotChart,
  prior: PivotChart,
  metricKey: string,
  priorLabel = "Previous period",
  priorBucketLabels?: string[],
): PivotChart {
  const priorKey = `prior_${metricKey}`;
  const pctKey = `pct_${metricKey}`;
  const data = current.data.map((row, i) => {
    const curRaw = row[metricKey];
    const curVal =
      curRaw == null || Number.isNaN(Number(curRaw)) ? null : Number(curRaw);
    const raw = i < prior.data.length ? prior.data[i]![metricKey] : null;
    const priorVal = raw == null || Number.isNaN(Number(raw)) ? null : Number(raw);
    return {
      ...row,
      [priorKey]: priorVal,
      [pctKey]: pctChange(curVal, priorVal),
      ...(priorBucketLabels?.[i] != null
        ? { prior_bucket: priorBucketLabels[i] }
        : {}),
    };
  });
  const series: Series[] = [
    ...current.series.map((s) => ({ ...s, yAxisId: s.yAxisId ?? ("left" as const) })),
    { key: priorKey, label: priorLabel, dashed: true, yAxisId: "left" },
    {
      key: pctKey,
      label: "% change",
      yAxisId: "right",
      color: "var(--chart-3)",
    },
  ];
  return { data, series };
}
