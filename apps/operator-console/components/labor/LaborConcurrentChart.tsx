"use client";

import { useMemo } from "react";
import { BarChartCard } from "@/components/charts/BarChartCard";
import { LABOR_CHART_COLORS } from "@/lib/charts/palette";
import type { Grain } from "@/lib/filters/range";
import { showsFullTime, showsPartTime } from "@/lib/filters/labor-type";

export type LaborConcurrentChartRow = {
  date: string;
  parttime_concurrent: number | null;
  fulltime_concurrent: number | null;
  total_concurrent: number | null;
  parttime_scheduled_concurrent?: number | null;
  fulltime_scheduled_concurrent?: number | null;
  total_scheduled_concurrent?: number | null;
};

const PT = LABOR_CHART_COLORS.parttimeActual;
const FT = LABOR_CHART_COLORS.fulltimeActual;
const PT_S = LABOR_CHART_COLORS.parttimeScheduled;
const FT_S = LABOR_CHART_COLORS.fulltimeScheduled;

function formatConcurrent(n: number | null | undefined): string {
  if (n == null || Number.isNaN(Number(n))) return "—";
  return Number(n).toFixed(1);
}

function meanNullable(
  a: number | null | undefined,
  b: number | null | undefined,
): number | null {
  const vals = [a, b].filter((x): x is number => x != null && !Number.isNaN(Number(x)));
  if (!vals.length) return null;
  return Number((vals.reduce((s, n) => s + Number(n), 0) / vals.length).toFixed(1));
}

function scopedConcurrent(
  ptVal: number | null | undefined,
  ftVal: number | null | undefined,
  totalVal: number | null | undefined,
  laborTypes: string[] | null,
): number | null {
  const pt = showsPartTime(laborTypes);
  const ft = showsFullTime(laborTypes);
  if (pt && ft) return totalVal ?? null;
  if (pt) return ptVal ?? null;
  if (ft) return ftVal ?? null;
  return null;
}

/**
 * Tooltip / legend "Total" when PT+FT both shown = sum of the stacked segments
 * (matches bar height). `total_*_concurrent` from hours÷span is NOT PT+FT and
 * was reading ~half the stacked bar (e.g. FT 2.4 while stack tops out at 4.4).
 */
export function stackOrScopedConcurrent(
  ptVal: number | null | undefined,
  ftVal: number | null | undefined,
  totalVal: number | null | undefined,
  laborTypes: string[] | null,
): number | null {
  const pt = showsPartTime(laborTypes);
  const ft = showsFullTime(laborTypes);
  if (pt && ft) {
    const hasPt = ptVal != null && !Number.isNaN(Number(ptVal));
    const hasFt = ftVal != null && !Number.isNaN(Number(ftVal));
    if (!hasPt && !hasFt) return totalVal ?? null;
    return Number(
      ((hasPt ? Number(ptVal) : 0) + (hasFt ? Number(ftVal) : 0)).toFixed(1),
    );
  }
  return scopedConcurrent(ptVal, ftVal, totalVal, laborTypes);
}

/**
 * Tooltip: actual avg, scheduled avg, combined (mean of the two when both exist).
 * Bars never stack schedule on actual.
 */
export function concurrentTooltipEntries(
  row: LaborConcurrentChartRow,
  laborTypes: string[] | null,
): { label: string; value: string; color?: string }[] {
  const pt = showsPartTime(laborTypes);
  const ft = showsFullTime(laborTypes);
  const entries: { label: string; value: string; color?: string }[] = [];

  const hasActual =
    (row.parttime_concurrent != null && row.parttime_concurrent > 0) ||
    (row.fulltime_concurrent != null && row.fulltime_concurrent > 0);
  const hasSched =
    (row.parttime_scheduled_concurrent != null &&
      row.parttime_scheduled_concurrent > 0) ||
    (row.fulltime_scheduled_concurrent != null &&
      row.fulltime_scheduled_concurrent > 0);

  if (hasActual) {
    if (pt) {
      entries.push({
        label: "Part-time (actual)",
        value: formatConcurrent(row.parttime_concurrent),
        color: PT,
      });
    }
    if (ft) {
      entries.push({
        label: "Full-time (actual)",
        value: formatConcurrent(row.fulltime_concurrent),
        color: FT,
      });
    }
    if (pt || ft) {
      entries.push({
        label: "Total (actual)",
        value: formatConcurrent(
          stackOrScopedConcurrent(
            row.parttime_concurrent,
            row.fulltime_concurrent,
            row.total_concurrent,
            laborTypes,
          ),
        ),
      });
    }
  }

  if (hasSched) {
    if (pt) {
      entries.push({
        label: "Part-time (scheduled)",
        value: formatConcurrent(row.parttime_scheduled_concurrent),
        color: PT_S,
      });
    }
    if (ft) {
      entries.push({
        label: "Full-time (scheduled)",
        value: formatConcurrent(row.fulltime_scheduled_concurrent),
        color: FT_S,
      });
    }
    if (pt || ft) {
      entries.push({
        label: "Total (scheduled)",
        value: formatConcurrent(
          stackOrScopedConcurrent(
            row.parttime_scheduled_concurrent,
            row.fulltime_scheduled_concurrent,
            row.total_scheduled_concurrent,
            laborTypes,
          ),
        ),
      });
    }
  }

  if (hasActual && hasSched && (pt || ft)) {
    const actualTotal = stackOrScopedConcurrent(
      row.parttime_concurrent,
      row.fulltime_concurrent,
      row.total_concurrent,
      laborTypes,
    );
    const schedTotal = stackOrScopedConcurrent(
      row.parttime_scheduled_concurrent,
      row.fulltime_scheduled_concurrent,
      row.total_scheduled_concurrent,
      laborTypes,
    );
    entries.push({
      label: "Total (combined)",
      value: formatConcurrent(meanNullable(actualTotal, schedTotal)),
    });
  }

  return entries;
}

/**
 * Bars = actual concurrent only (schedule in tooltip). Schedule-only buckets
 * still get slate bars so future days aren't blank.
 */
export function LaborConcurrentChart({
  data,
  laborTypes,
  grain,
  titlePrefix = "",
  subtitle,
}: {
  data: LaborConcurrentChartRow[];
  laborTypes: string[] | null;
  grain: Grain;
  titlePrefix?: string;
  subtitle?: string;
}) {
  const { chartData, series, title, stacked } = useMemo(() => {
    const pt = showsPartTime(laborTypes);
    const ft = showsFullTime(laborTypes);
    const neither = !pt && !ft;

    const hasAnyActual = data.some(
      (r) =>
        (r.parttime_concurrent != null && r.parttime_concurrent > 0) ||
        (r.fulltime_concurrent != null && r.fulltime_concurrent > 0),
    );
    const hasSchedOnlyBuckets = data.some((r) => {
      const a =
        (r.parttime_concurrent != null && r.parttime_concurrent > 0) ||
        (r.fulltime_concurrent != null && r.fulltime_concurrent > 0);
      const s =
        (r.parttime_scheduled_concurrent != null &&
          r.parttime_scheduled_concurrent > 0) ||
        (r.fulltime_scheduled_concurrent != null &&
          r.fulltime_scheduled_concurrent > 0);
      return s && !a;
    });

    const series: { key: string; label: string; color: string }[] = [];
    if (!neither) {
      if (hasAnyActual && pt) {
        series.push({ key: "parttime", label: "Part-time", color: PT });
      }
      if (hasAnyActual && ft) {
        series.push({ key: "fulltime", label: "Full-time", color: FT });
      }
      if (hasSchedOnlyBuckets && pt) {
        series.push({
          key: "parttime_sched",
          label: "Part-time (scheduled)",
          color: PT_S,
        });
      }
      if (hasSchedOnlyBuckets && ft) {
        series.push({
          key: "fulltime_sched",
          label: "Full-time (scheduled)",
          color: FT_S,
        });
      }
    }

    const chartData = data.map((r) => {
      const hasActual =
        (r.parttime_concurrent != null && r.parttime_concurrent > 0) ||
        (r.fulltime_concurrent != null && r.fulltime_concurrent > 0);
      const showSchedBar = !hasActual;
      return {
        date: r.date,
        parttime: pt ? r.parttime_concurrent : null,
        fulltime: ft ? r.fulltime_concurrent : null,
        parttime_sched:
          pt && showSchedBar ? (r.parttime_scheduled_concurrent ?? null) : null,
        fulltime_sched:
          ft && showSchedBar ? (r.fulltime_scheduled_concurrent ?? null) : null,
        tooltipEntries: concurrentTooltipEntries(r, laborTypes),
      };
    });

    const base =
      grain === "day"
        ? "Avg concurrent on floor"
        : grain === "hour"
          ? "Concurrent on floor"
          : "Avg concurrent on floor / day";
    const title = neither ? `${titlePrefix}${base}` : `${titlePrefix}${base} by ${grain}`;

    return {
      chartData,
      series,
      title,
      // Stack PT+FT (actual and/or scheduled) so bar height = tooltip Total.
      stacked: series.length > 1,
    };
  }, [data, grain, laborTypes, titlePrefix]);

  if (series.length === 0) return null;

  return (
    <BarChartCard
      title={title}
      subtitle={subtitle}
      data={chartData}
      xKey="date"
      series={series}
      stacked={stacked}
      valueFormat="number"
    />
  );
}
