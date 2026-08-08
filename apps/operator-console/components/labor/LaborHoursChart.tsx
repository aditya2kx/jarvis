"use client";

import { useMemo } from "react";
import { BarChartCard } from "@/components/charts/BarChartCard";
import { LABOR_CHART_COLORS } from "@/lib/charts/palette";
import type { Grain } from "@/lib/filters/range";
import { showsFullTime, showsPartTime } from "@/lib/filters/labor-type";
import type { LaborChartUnit } from "@/lib/filters/labor-chart-unit";
import { grainDisplayLabel } from "@/lib/filters/range";

export type LaborHoursChartRow = {
  date: string;
  /** ISO bucket date (YYYY-MM-DD). */
  bucket_iso: string;
  /** Clocked hours (dates < today CT). */
  total_hours: number | null;
  parttime_hours: number | null;
  fulltime_hours: number | null;
  /** Fraction 0–1 from BQ — actuals only; null for schedule-only buckets. */
  labor_pct: number | null;
  hourly_pct: number | null;
  fulltime_pct: number | null;
  net_sales: number | null;
  /** Scheduled hours (dates ≥ today CT) — stacked on hours bars; no labor %. */
  parttime_scheduled_hours?: number | null;
  fulltime_scheduled_hours?: number | null;
};

export type LaborTooltipEntry = {
  label: string;
  value: string;
  color?: string;
};

/** Weekly hours goal applies only at Aggregation=week — never scaled to day/month. */
export function weeklyHoursGoalApplicable(grain: Grain): boolean {
  return grain === "week";
}

/**
 * What % of net sales the absolute hours goal implies at this bucket's blended
 * wage: (goalHours / actualHours) × laborPct × 100.
 */
export function goalHoursAsSalesPct(
  goalHours: number | null,
  actualHours: number | null,
  laborPct: number | null,
): number | null {
  if (goalHours == null || actualHours == null || laborPct == null) return null;
  const gh = Number(goalHours);
  const ah = Number(actualHours);
  const lp = Number(laborPct);
  if (!(gh > 0) || !(ah > 0) || !(lp > 0) || Number.isNaN(lp)) return null;
  return Number(((gh / ah) * lp * 100).toFixed(1));
}

/** Combined (or actual/scheduled) hours as % of the weekly hours goal. */
export function pctOfHoursGoal(
  combinedHours: number | null,
  goalHours: number | null,
): number | null {
  if (combinedHours == null || goalHours == null) return null;
  const c = Number(combinedHours);
  const g = Number(goalHours);
  if (!(g > 0) || Number.isNaN(c) || Number.isNaN(g)) return null;
  return Number(((c / g) * 100).toFixed(1));
}

function formatHours(n: number | null): string {
  if (n == null || Number.isNaN(Number(n))) return "—";
  return Number(n).toLocaleString("en-US", { maximumFractionDigits: 1 });
}

/** `1,006.5 (31.4%)` — hours with labor % of Square net sales in brackets. */
export function formatHoursWithPct(hours: number | null, laborPct: number | null): string {
  const h = formatHours(hours);
  if (laborPct == null || Number.isNaN(Number(laborPct))) return h;
  return `${h} (${(Number(laborPct) * 100).toFixed(1)}%)`;
}

/** Fraction 0–1 → `"31.4%"`; null → em dash. */
export function formatLaborPct(laborPct: number | null): string {
  if (laborPct == null || Number.isNaN(Number(laborPct))) return "—";
  return `${(Number(laborPct) * 100).toFixed(1)}%`;
}

/** BQ fraction → chart percent (31.4). */
export function laborPctToChart(laborPct: number | null): number | null {
  if (laborPct == null || Number.isNaN(Number(laborPct))) return null;
  return Number((Number(laborPct) * 100).toFixed(2));
}

/**
 * Tooltip for % of net sales mode — PT/FT/Total percents only (completed actuals).
 * No schedule / hours goal lines.
 */
export function laborPctTooltipContent(
  row: LaborHoursChartRow,
  laborTypes: string[] | null,
): { entries: LaborTooltipEntry[]; lines: string[] } {
  const ptOn = showsPartTime(laborTypes);
  const ftOn = showsFullTime(laborTypes);
  const entries: LaborTooltipEntry[] = [];

  const hasPct =
    (row.hourly_pct != null && !Number.isNaN(Number(row.hourly_pct))) ||
    (row.fulltime_pct != null && !Number.isNaN(Number(row.fulltime_pct))) ||
    (row.labor_pct != null && !Number.isNaN(Number(row.labor_pct)));

  if (hasPct) {
    if (ptOn) {
      entries.push({
        label: "Part-time",
        value: formatLaborPct(row.hourly_pct),
        color: PT,
      });
    }
    if (ftOn) {
      entries.push({
        label: "Full-time",
        value: formatLaborPct(row.fulltime_pct),
        color: FT,
      });
    }
    const scoped = scopedLaborMetrics(row, laborTypes);
    if (ptOn || ftOn) {
      entries.push({
        label: "Total",
        value: formatLaborPct(scoped.laborPct),
      });
    }
  }

  const lines: string[] = [];
  if (!hasPct) {
    lines.push("No completed labor % (need Square net sales on completed days)");
  }
  return { entries, lines };
}

function sumNullable(a: number | null | undefined, b: number | null | undefined): number | null {
  if (a == null && b == null) return null;
  return Number(((a ?? 0) + (b ?? 0)).toFixed(1));
}

/** Hours / labor-% scope for the current labor-type filter (actuals only). */
export function scopedLaborMetrics(
  row: Pick<
    LaborHoursChartRow,
    "total_hours" | "parttime_hours" | "fulltime_hours" | "labor_pct" | "hourly_pct" | "fulltime_pct"
  >,
  laborTypes: string[] | null,
): { hours: number | null; laborPct: number | null } {
  const pt = showsPartTime(laborTypes);
  const ft = showsFullTime(laborTypes);
  if (pt && ft) {
    return { hours: row.total_hours, laborPct: row.labor_pct };
  }
  if (pt) return { hours: row.parttime_hours, laborPct: row.hourly_pct };
  if (ft) return { hours: row.fulltime_hours, laborPct: row.fulltime_pct };
  return { hours: null, laborPct: null };
}

function scopedScheduledHours(
  row: LaborHoursChartRow,
  laborTypes: string[] | null,
): number | null {
  const pt = showsPartTime(laborTypes);
  const ft = showsFullTime(laborTypes);
  const schedPt = row.parttime_scheduled_hours ?? null;
  const schedFt = row.fulltime_scheduled_hours ?? null;
  if (pt && ft) return sumNullable(schedPt, schedFt);
  if (pt) return schedPt;
  if (ft) return schedFt;
  return null;
}

const PT = LABOR_CHART_COLORS.parttimeActual;
const FT = LABOR_CHART_COLORS.fulltimeActual;
const PT_S = LABOR_CHART_COLORS.parttimeScheduled;
const FT_S = LABOR_CHART_COLORS.fulltimeScheduled;

/**
 * Tooltip: actual (bars) + scheduled (hover only) + combined total vs weekly Goal.
 */
export function laborTooltipContent(
  row: LaborHoursChartRow,
  goalLaborHoursWeek: number | null | undefined,
  grain: Grain,
  laborTypes: string[] | null,
): { entries: LaborTooltipEntry[]; lines: string[] } {
  const ptOn = showsPartTime(laborTypes);
  const ftOn = showsFullTime(laborTypes);
  const entries: LaborTooltipEntry[] = [];

  const hasActual =
    (row.parttime_hours != null && row.parttime_hours > 0) ||
    (row.fulltime_hours != null && row.fulltime_hours > 0) ||
    (row.total_hours != null && row.total_hours > 0);
  const schedPt = row.parttime_scheduled_hours ?? null;
  const schedFt = row.fulltime_scheduled_hours ?? null;
  const hasSched =
    (schedPt != null && schedPt > 0) || (schedFt != null && schedFt > 0);

  if (hasActual) {
    if (ptOn) {
      entries.push({
        label: "Part-time (actual)",
        value: formatHoursWithPct(row.parttime_hours, row.hourly_pct),
        color: PT,
      });
    }
    if (ftOn) {
      entries.push({
        label: "Full-time (actual)",
        value: formatHoursWithPct(row.fulltime_hours, row.fulltime_pct),
        color: FT,
      });
    }
    const scoped = scopedLaborMetrics(row, laborTypes);
    if (ptOn || ftOn) {
      entries.push({
        label: "Total (actual)",
        value: formatHoursWithPct(scoped.hours, scoped.laborPct),
      });
    }
  }

  if (hasSched) {
    if (ptOn) {
      entries.push({
        label: "Part-time (scheduled)",
        value: formatHours(schedPt),
        color: PT_S,
      });
    }
    if (ftOn) {
      entries.push({
        label: "Full-time (scheduled)",
        value: formatHours(schedFt),
        color: FT_S,
      });
    }
    if (ptOn || ftOn) {
      entries.push({
        label: "Total (scheduled)",
        value: formatHours(sumNullable(schedPt, schedFt)),
      });
    }
  }

  const actualHrs = scopedLaborMetrics(row, laborTypes).hours;
  const schedHrs = scopedScheduledHours(row, laborTypes);
  const combined = sumNullable(actualHrs, schedHrs);
  if (hasActual && hasSched && combined != null) {
    entries.push({
      label: "Total (combined)",
      value: formatHours(combined),
    });
  }

  const lines: string[] = [];
  if (hasSched && !hasActual) {
    lines.push("Scheduled — no labor % (no Square sales yet)");
  }
  if (
    weeklyHoursGoalApplicable(grain) &&
    goalLaborHoursWeek != null &&
    !Number.isNaN(Number(goalLaborHoursWeek))
  ) {
    const goalHrs = Number(goalLaborHoursWeek);
    const vsGoalHrs = combined ?? actualHrs ?? schedHrs;
    const ofGoal = pctOfHoursGoal(vsGoalHrs, goalHrs);
    // Completed weeks only (no scheduled remainder): also show what 230 hrs
    // would be as % of that week's Square net sales.
    const completedWeek = hasActual && !hasSched;
    const scoped = scopedLaborMetrics(row, laborTypes);
    const ofSales = completedWeek
      ? goalHoursAsSalesPct(goalHrs, scoped.hours, scoped.laborPct)
      : null;

    const parts: string[] = [];
    if (ofGoal != null) parts.push(`${ofGoal.toFixed(1)}% of goal`);
    if (ofSales != null) parts.push(`${ofSales.toFixed(1)}% of sales`);
    if (parts.length) {
      lines.push(`Goal ${formatHours(goalHrs)} hrs (${parts.join(" · ")})`);
    } else {
      lines.push(`Goal ${formatHours(goalHrs)} hrs`);
    }
  }
  return { entries, lines };
}

/**
 * Bars = actual + scheduled stacked (slate on top). Weekly Goal = gold dashed line.
 * When `unit=pct`, bars are labor % of Square net sales (completed actuals only —
 * no schedule stacks / hours goal). (Avg concurrent keeps schedule in the tooltip
 * only — different chart.)
 */
export function LaborHoursChart({
  data,
  laborTypes,
  grain,
  goalLaborHoursWeek,
  unit = "hours",
  titlePrefix = "",
  subtitle,
}: {
  data: LaborHoursChartRow[];
  laborTypes: string[] | null;
  grain: Grain;
  goalLaborHoursWeek?: number;
  unit?: LaborChartUnit;
  titlePrefix?: string;
  subtitle?: string;
}) {
  const { chartData, series, title, stacked, goal, goalLabel, valueFormat } = useMemo(() => {
    const pt = showsPartTime(laborTypes);
    const ft = showsFullTime(laborTypes);
    const neither = !pt && !ft;
    const pctMode = unit === "pct";
    const grainNoun = grainDisplayLabel(grain);

    const hasAnyActual = data.some(
      (r) =>
        (r.parttime_hours != null && r.parttime_hours > 0) ||
        (r.fulltime_hours != null && r.fulltime_hours > 0),
    );
    const hasAnyPct = data.some(
      (r) =>
        (r.hourly_pct != null && Number(r.hourly_pct) > 0) ||
        (r.fulltime_pct != null && Number(r.fulltime_pct) > 0) ||
        (r.labor_pct != null && Number(r.labor_pct) > 0),
    );
    const hasAnySched =
      !pctMode &&
      data.some(
        (r) =>
          (r.parttime_scheduled_hours != null && r.parttime_scheduled_hours > 0) ||
          (r.fulltime_scheduled_hours != null && r.fulltime_scheduled_hours > 0),
      );

    const series: { key: string; label: string; color: string }[] = [];
    if (!neither) {
      if (pctMode) {
        if (hasAnyPct && pt) {
          series.push({ key: "parttime", label: "Part-time", color: PT });
        }
        if (hasAnyPct && ft) {
          series.push({ key: "fulltime", label: "Full-time", color: FT });
        }
      } else {
        if (hasAnyActual && pt) {
          series.push({ key: "parttime", label: "Part-time", color: PT });
        }
        if (hasAnyActual && ft) {
          series.push({ key: "fulltime", label: "Full-time", color: FT });
        }
        if (hasAnySched && pt) {
          series.push({
            key: "parttime_sched",
            label: "Part-time (scheduled)",
            color: PT_S,
          });
        }
        if (hasAnySched && ft) {
          series.push({
            key: "fulltime_sched",
            label: "Full-time (scheduled)",
            color: FT_S,
          });
        }
      }
    }

    const chartData = data.map((r) => {
      if (pctMode) {
        const tip = laborPctTooltipContent(r, laborTypes);
        return {
          date: r.date,
          parttime: pt ? laborPctToChart(r.hourly_pct) : null,
          fulltime: ft ? laborPctToChart(r.fulltime_pct) : null,
          parttime_sched: null,
          fulltime_sched: null,
          tooltipEntries: tip.entries,
          tooltipLines: tip.lines,
        };
      }
      const tip = laborTooltipContent(r, goalLaborHoursWeek, grain, laborTypes);
      return {
        date: r.date,
        parttime: pt ? r.parttime_hours : null,
        fulltime: ft ? r.fulltime_hours : null,
        parttime_sched: pt ? (r.parttime_scheduled_hours ?? null) : null,
        fulltime_sched: ft ? (r.fulltime_scheduled_hours ?? null) : null,
        tooltipEntries: tip.entries,
        tooltipLines: tip.lines,
      };
    });

    const title = pctMode
      ? `${titlePrefix}Labor % of net sales by ${grainNoun}`
      : `${titlePrefix}Labor hours by ${grainNoun}`;

    const showGoal =
      !pctMode &&
      weeklyHoursGoalApplicable(grain) &&
      goalLaborHoursWeek != null &&
      !Number.isNaN(Number(goalLaborHoursWeek)) &&
      Number(goalLaborHoursWeek) > 0;

    return {
      chartData,
      series,
      title,
      stacked: series.length > 1,
      goal: showGoal ? Number(goalLaborHoursWeek) : undefined,
      goalLabel: showGoal ? `Goal ${Number(goalLaborHoursWeek)} hrs` : undefined,
      valueFormat: pctMode ? ("percent" as const) : ("number" as const),
    };
  }, [data, grain, goalLaborHoursWeek, laborTypes, titlePrefix, unit]);

  if (series.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        {unit === "pct"
          ? "No completed labor % in this Period — pick Part-time and/or Full-time, or switch to Hours."
          : "No labor type selected — pick Part-time and/or Full-time in the Labor type filter."}
      </p>
    );
  }

  return (
    <BarChartCard
      title={title}
      subtitle={subtitle}
      data={chartData}
      xKey="date"
      series={series}
      stacked={stacked}
      valueFormat={valueFormat}
      goal={goal}
      goalLabel={goalLabel}
      goalStroke={LABOR_CHART_COLORS.goalLine}
    />
  );
}
