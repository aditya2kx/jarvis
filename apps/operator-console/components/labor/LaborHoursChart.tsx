"use client";

import { useMemo, useState } from "react";
import { BarChartCard } from "@/components/charts/BarChartCard";
import { cn } from "@/lib/utils";
import type { Grain } from "@/lib/filters/range";

export type LaborChartUnit = "hours" | "pct_net_sales";

export type LaborHoursChartRow = {
  date: string;
  total_hours: number | null;
  parttime_hours: number | null;
  fulltime_hours: number | null;
  /** Fraction 0–1 from BQ; displayed as percent points. */
  labor_pct: number | null;
  hourly_pct: number | null;
  fulltime_pct: number | null;
};

function toPctPoints(fraction: number | null): number | null {
  if (fraction == null || Number.isNaN(Number(fraction))) return null;
  return Number((Number(fraction) * 100).toFixed(1));
}

/**
 * L1 Labor chart: Hours vs % of Square net sales (labor $ / net sales),
 * Aggregate vs PT/FT breakdown (breakdown from URL; unit is client toggle).
 */
export function LaborHoursChart({
  data,
  breakdown,
  grain,
  goalLaborPct,
}: {
  data: LaborHoursChartRow[];
  breakdown: boolean;
  grain: Grain;
  /** Goal as fraction 0–1 (store_config goal_labor_pct_max). */
  goalLaborPct?: number;
}) {
  const [unit, setUnit] = useState<LaborChartUnit>("hours");
  const asPct = unit === "pct_net_sales";

  const { chartData, series, title, valueFormat, goal } = useMemo(() => {
    if (asPct) {
      const chartData = data.map((r) => ({
        date: r.date,
        total: toPctPoints(r.labor_pct),
        parttime: toPctPoints(r.hourly_pct),
        fulltime: toPctPoints(r.fulltime_pct),
      }));
      return {
        chartData,
        series: breakdown
          ? [
              { key: "parttime", label: "Part-time %" },
              { key: "fulltime", label: "Full-time %" },
            ]
          : [{ key: "total", label: "Labor %" }],
        title: breakdown
          ? `Labor % of Square net sales — PT/FT by ${grain}`
          : `Labor % of Square net sales by ${grain}`,
        valueFormat: "percent" as const,
        goal: goalLaborPct != null ? goalLaborPct * 100 : undefined,
      };
    }
    const chartData = data.map((r) => ({
      date: r.date,
      total: r.total_hours,
      parttime: r.parttime_hours,
      fulltime: r.fulltime_hours,
    }));
    return {
      chartData,
      series: breakdown
        ? [
            { key: "parttime", label: "Part-time" },
            { key: "fulltime", label: "Full-time" },
          ]
        : [{ key: "total", label: "Hours" }],
      title: breakdown
        ? `Labor hours — PT/FT by ${grain}`
        : `Labor hours by ${grain}`,
      valueFormat: "number" as const,
      goal: undefined,
    };
  }, [asPct, breakdown, data, grain, goalLaborPct]);

  const unitToggle = (
    <div className="flex items-center gap-1 rounded-md bg-secondary p-0.5">
      {(
        [
          { value: "hours" as const, label: "Hours" },
          { value: "pct_net_sales" as const, label: "% of Square net sales" },
        ] as const
      ).map((opt) => (
        <button
          key={opt.value}
          type="button"
          className={cn(
            "rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors",
            unit === opt.value
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
          onClick={() => setUnit(opt.value)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );

  return (
    <BarChartCard
      title={title}
      data={chartData}
      xKey="date"
      series={series}
      stacked={breakdown && !asPct}
      valueFormat={valueFormat}
      goal={goal}
      goalLabel="Goal"
      headerRight={unitToggle}
    />
  );
}
