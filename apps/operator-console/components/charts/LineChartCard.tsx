"use client";

import { useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useIsPhone } from "@/components/shell/useIsPhone";
import { LABOR_CHART_COLORS, seriesColorAt } from "@/lib/charts/palette";
import {
  FloatingReadout,
  ReadoutSlot,
  ScrubReadout,
  SeriesToggles,
  useHiddenSeries,
  type Readout,
} from "./ChartInteraction";

export interface Series {
  key: string;
  label: string;
  color?: string;
  dashed?: boolean;
  /** When set, overrides BarChartCard `stacked` for this series (Recharts stackId). */
  stackId?: string;
  /** Dual-axis: absolute series stay on left; % change uses right. */
  yAxisId?: "left" | "right";
  /**
   * BarChartCard only: `hatch` = diagonal hatch + dashed outline; `outline` = faint
   * fill + dashed outline (proposed, not yet booked).
   */
  pattern?: "hatch" | "outline";
}

export type LineValueFormat = "dollars" | "percent" | "number";

function formatAbs(value: unknown, format: LineValueFormat = "number"): string {
  if (value == null || value === "") return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(n)) return "—";
  if (format === "percent") return `${n.toFixed(1)}%`;
  if (format === "dollars") {
    return n.toLocaleString("en-US", { style: "currency", currency: "USD" });
  }
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function formatTick(value: number, format: LineValueFormat): string {
  if (format === "percent") return `${Number(value).toFixed(0)}%`;
  if (format === "number") {
    const n = Number(value);
    if (Math.abs(n) >= 1000) return `${(n / 1000).toFixed(n % 1000 === 0 ? 0 : 1)}k`;
    return `${Math.round(n)}`;
  }
  const n = Number(value);
  const sign = n < 0 ? "−" : "";
  const abs = Math.abs(n);
  if (abs >= 1000) return `${sign}$${(abs / 1000).toFixed(abs % 1000 === 0 ? 0 : 1)}k`;
  return `${sign}$${abs.toFixed(0)}`;
}

function formatPct(value: unknown): string {
  if (value == null || value === "") return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(n)) return "—";
  const rounded = Math.round(n * 10) / 10;
  const sign = rounded > 0 ? "+" : "";
  return `${sign}${rounded.toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
}

function lineColor(s: Series, i: number): string {
  return s.color ?? seriesColorAt(i);
}

/** Visible series at one x; Compare rows also carry `prior_bucket` and a `pct_*` change. */
function lineReadout(
  label: unknown,
  row: Record<string, unknown> | undefined,
  series: Series[],
  hidden: ReadonlySet<string>,
  format: LineValueFormat,
): Readout | null {
  if (!row) return null;
  const priorBucket = row.prior_bucket;
  const pctEntry = Object.entries(row).find(([k, v]) => k.startsWith("pct_") && v != null);
  return {
    title:
      typeof priorBucket === "string" && priorBucket
        ? `${String(label ?? "")}  ·  vs ${priorBucket}`
        : String(label ?? ""),
    rows: series
      .map((s, i) => ({ s, color: lineColor(s, i) }))
      .filter(({ s }) => !hidden.has(s.key) && !s.key.startsWith("pct_"))
      .map(({ s, color }) => ({ label: s.label, value: formatAbs(row[s.key], format), color })),
    notes: pctEntry ? [`% change: ${formatPct(pctEntry[1])}`] : undefined,
  };
}

// Dashed goal line: the only "computed" thing here is a visual comparison of
// already-fetched rows against a goal value — no new metric math (see
// EXECUTION.md §4 M2 step 4 — this is components/charts/GoalLine.tsx inlined
// as a prop since Recharts ReferenceLine needs no extra component).
export function LineChartCard({
  title,
  subtitle,
  data,
  xKey,
  series,
  goal,
  goalLabel,
  height = 260,
  valueFormat,
}: {
  title: string;
  /** Optional second line under the title (e.g. prior window dates). */
  subtitle?: string;
  data: Record<string, unknown>[];
  xKey: string;
  series: Series[];
  goal?: number;
  goalLabel?: string;
  height?: number;
  /**
   * When omitted, left-axis ticks stay Recharts defaults (pre-#231 behavior).
   * Sales Trend passes dollars/number explicitly.
   */
  valueFormat?: LineValueFormat;
}) {
  const hasRight = series.some((s) => s.yAxisId === "right");
  const tipFormat: LineValueFormat = valueFormat ?? "number";
  const phone = useIsPhone();
  const { hidden, toggle } = useHiddenSeries();
  const [readoutEl, setReadoutEl] = useState<HTMLDivElement | null>(null);
  const readoutFor = (label: unknown, row: Record<string, unknown> | undefined) =>
    lineReadout(label, row, series, hidden, tipFormat);

  return (
    <Card>
      <CardHeader className="gap-1">
        <CardTitle className="text-sm font-semibold">{title}</CardTitle>
        {subtitle ? (
          <p className="text-xs text-muted-foreground">{subtitle}</p>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <SeriesToggles
          items={series
            .map((s, i) => ({ key: s.key, label: s.label, color: lineColor(s, i) }))
            .filter((item) => !item.key.startsWith("pct_"))}
          hidden={hidden}
          onToggle={toggle}
        />
        {phone ? <ReadoutSlot onMount={setReadoutEl} /> : null}
        <ResponsiveContainer width="100%" height={height} className="touch-pan-y">
          <LineChart
            data={data}
            margin={{ top: 8, right: hasRight ? 8 : 12, left: -12, bottom: 0 }}
          >
            <CartesianGrid vertical={false} className="stroke-border/70" />
            <XAxis dataKey={xKey} tick={{ fontSize: 12 }} tickLine={false} axisLine={false} />
            <YAxis
              yAxisId="left"
              tick={{ fontSize: 12 }}
              tickLine={false}
              axisLine={false}
              tickFormatter={
                valueFormat
                  ? (v) => formatTick(Number(v), valueFormat)
                  : undefined
              }
            />
            {hasRight ? (
              <YAxis
                yAxisId="right"
                orientation="right"
                tick={{ fontSize: 12 }}
                tickLine={false}
                axisLine={false}
                tickFormatter={(v) => `${v}%`}
                width={48}
              />
            ) : null}
            <Tooltip
              content={({ active, label, payload }) =>
                phone || !active ? null : (
                  <FloatingReadout
                    readout={readoutFor(label, payload?.[0]?.payload as Record<string, unknown> | undefined)}
                  />
                )
              }
            />
            {phone ? (
              <ScrubReadout
                target={readoutEl}
                build={(label) => readoutFor(label, data.find((row) => String(row[xKey] ?? "") === label))}
              />
            ) : null}
            {series.map((s, i) => (
              <Line
                key={s.key}
                yAxisId={s.yAxisId ?? "left"}
                type="monotone"
                dataKey={s.key}
                name={s.label}
                hide={hidden.has(s.key)}
                stroke={lineColor(s, i)}
                strokeWidth={s.yAxisId === "right" ? 1.5 : 2}
                strokeDasharray={s.dashed ? "6 4" : undefined}
                dot={false}
                // Gaps (null) must not draw a fake flat $0 line across empty history.
                connectNulls={false}
              />
            ))}
            {goal != null ? (
              <ReferenceLine
                yAxisId="left"
                y={goal}
                stroke={LABOR_CHART_COLORS.goalLine}
                strokeDasharray="4 4"
                strokeWidth={1.5}
                label={{ value: goalLabel ?? "Goal", position: "insideTopRight", fontSize: 11, fill: LABOR_CHART_COLORS.goalLine }}
              />
            ) : null}
          </LineChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}
