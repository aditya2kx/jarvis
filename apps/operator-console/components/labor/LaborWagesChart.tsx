"use client";

import { useMemo } from "react";
import { BarChartCard } from "@/components/charts/BarChartCard";
import type { Series } from "@/components/charts/LineChartCard";
import { LABOR_CHART_COLORS } from "@/lib/charts/palette";
import { grainDisplayLabel, type Grain } from "@/lib/filters/range";
import { showsFullTime, showsPartTime } from "@/lib/filters/labor-type";
import { useSuggestedHours } from "@/components/labor/SuggestedHoursContext";
import { bucketDaily } from "@/lib/labor/suggested-buckets";
import type { LaborTooltipEntry } from "@/components/labor/LaborHoursChart";

/** Wage dollars per bucket — actuals priced at the rate in effect on each shift date. */
export type LaborWagesChartRow = {
  date: string;
  bucket_iso: string;
  parttime_cost: number | null;
  fulltime_cost: number | null;
  net_sales: number | null;
  parttime_sched_cost: number | null;
  fulltime_sched_cost: number | null;
  /** ADP open shifts at the average part-time rate. */
  open_cost: number | null;
};

export type WageRates = { byName: Record<string, number>; avgPartTime: number | null };

/** Price each day's draft hours: the suggested person's rate, else the average part-time rate. */
export function suggestedCostByDay(
  byDayPerson: ReadonlyMap<string, ReadonlyMap<string, number>>,
  rates: WageRates,
): Map<string, number> {
  const out = new Map<string, number>();
  for (const [day, people] of byDayPerson) {
    let cost = 0;
    for (const [name, hours] of people) cost += hours * (rates.byName[name] ?? rates.avgPartTime ?? 0);
    if (cost > 0) out.set(day, cost);
  }
  return out;
}

const fmt$ = (n: number | null | undefined) =>
  n == null || Number.isNaN(n) ? "—" : `$${Math.round(n).toLocaleString("en-US")}`;

function add(...xs: (number | null | undefined)[]): number | null {
  return xs.some((x) => x != null) ? xs.reduce<number>((a, x) => a + (x ?? 0), 0) : null;
}

const PT = LABOR_CHART_COLORS.parttimeActual;
const FT = LABOR_CHART_COLORS.fulltimeActual;
const PT_S = LABOR_CHART_COLORS.parttimeScheduled;
const FT_S = LABOR_CHART_COLORS.fulltimeScheduled;
const DRAFT = LABOR_CHART_COLORS.draftShift;

/**
 * Same stacking and buckets as Labor hours: actual wages (solid), scheduled
 * estimate (slate), open shifts (violet hatch) and suggested draft shifts.
 */
export function LaborWagesChart({
  data,
  rates,
  laborTypes,
  grain,
  titlePrefix = "",
  subtitle,
  stat,
  period,
}: {
  data: LaborWagesChartRow[];
  rates: WageRates;
  laborTypes: string[] | null;
  grain: Grain;
  titlePrefix?: string;
  subtitle?: string;
  stat?: "avg" | "total";
  period?: { start: string; end: string };
}) {
  const { byDayPerson } = useSuggestedHours();
  const { chartData, series } = useMemo(() => {
    const pt = showsPartTime(laborTypes);
    const ft = showsFullTime(laborTypes);
    const suggested = bucketDaily(
      suggestedCostByDay(byDayPerson, rates),
      new Set(data.map((r) => r.bucket_iso)),
      grain,
      stat === "avg" && (grain === "weekday" || grain === "all"),
      period ?? { start: "", end: "" },
    );
    const rows = data.map((r) => {
      const sug = pt || ft ? (suggested.get(r.bucket_iso) ?? null) : null;
      const actPt = pt ? r.parttime_cost : null;
      const actFt = ft ? r.fulltime_cost : null;
      const schPt = pt ? r.parttime_sched_cost : null;
      const schFt = ft ? r.fulltime_sched_cost : null;
      const open = pt || ft ? r.open_cost : null;
      const actual = add(actPt, actFt);
      const sched = add(schPt, schFt);
      const entries: LaborTooltipEntry[] = [];
      if (actual) {
        if (pt) entries.push({ label: "Part-time (actual)", value: fmt$(actPt), color: PT });
        if (ft) entries.push({ label: "Full-time (actual)", value: fmt$(actFt), color: FT });
        const pct = r.net_sales ? ` (${((actual / r.net_sales) * 100).toFixed(1)}% of sales)` : "";
        entries.push({ label: "Total (actual)", value: `${fmt$(actual)}${pct}` });
      }
      if (sched) {
        if (pt) entries.push({ label: "Part-time (scheduled)", value: fmt$(schPt), color: PT_S });
        if (ft) entries.push({ label: "Full-time (scheduled)", value: fmt$(schFt), color: FT_S });
      }
      if (open) entries.push({ label: "Open (unassigned)", value: fmt$(open), color: LABOR_CHART_COLORS.openShiftSwatch });
      if (sug) entries.push({ label: "Suggested (draft)", value: fmt$(sug), color: DRAFT });
      const estimated = Boolean(sched || open || sug);
      if (estimated) entries.push({ label: "Total (estimated)", value: fmt$(add(actual, sched, open, sug)) });
      const round = (n: number | null) => (n != null ? Math.round(n) : null);
      return {
        date: r.date,
        parttime: round(actPt),
        fulltime: round(actFt),
        parttime_sched: round(schPt),
        fulltime_sched: round(schFt),
        open: round(open),
        suggested: round(sug),
        tooltipEntries: entries,
        tooltipLines: estimated
          ? ["Scheduled at each person's rate; open and unassigned shifts at the average part-time rate"]
          : [],
      };
    });
    const has = (k: keyof (typeof rows)[number]) => rows.some((r) => ((r[k] as number | null) ?? 0) > 0);
    const s: Series[] = [];
    if (pt && has("parttime")) s.push({ key: "parttime", label: "Part-time", color: PT });
    if (ft && has("fulltime")) s.push({ key: "fulltime", label: "Full-time", color: FT });
    if (pt && has("parttime_sched")) s.push({ key: "parttime_sched", label: "Part-time (scheduled)", color: PT_S });
    if (ft && has("fulltime_sched")) s.push({ key: "fulltime_sched", label: "Full-time (scheduled)", color: FT_S });
    if (has("open")) s.push({ key: "open", label: "Open (unassigned)", color: LABOR_CHART_COLORS.openShift, pattern: "hatch" });
    if (has("suggested")) s.push({ key: "suggested", label: "Suggested (draft)", color: DRAFT, pattern: "hatch" });
    return { chartData: rows, series: s };
  }, [data, rates, byDayPerson, laborTypes, grain, stat, period]);

  if (!series.length) {
    return (
      <p className="text-sm text-muted-foreground">
        No wages in this Period — pick Part-time and/or Full-time in the Labor type filter.
      </p>
    );
  }
  return (
    <BarChartCard
      title={`${titlePrefix}Labor wages by ${grainDisplayLabel(grain)}`}
      subtitle={subtitle}
      data={chartData}
      xKey="date"
      series={series}
      stacked={series.length > 1}
      valueFormat="dollars"
    />
  );
}
