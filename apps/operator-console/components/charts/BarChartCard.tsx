"use client";

import type { ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Series } from "./LineChartCard";
import { chartColorAt } from "@/lib/charts/palette";

export type BarValueFormat = "dollars" | "percent";

type TooltipRow = {
  dataKey?: string | number;
  name?: string;
  value?: number | string;
  color?: string;
  fill?: string;
};

/** Per-bar fill from the signed value (e.g. cash flow green/red). */
export type SignedValueColors = {
  dataKey: string;
  positive: string;
  negative: string;
  zero?: string;
};

function formatTick(value: number, format: BarValueFormat): string {
  if (format === "percent") {
    return `${Number(value).toFixed(0)}%`;
  }
  const n = Number(value);
  if (Math.abs(n) >= 1000) return `$${(n / 1000).toFixed(n % 1000 === 0 ? 0 : 1)}k`;
  return `$${n.toFixed(0)}`;
}

function formatTooltipValue(value: unknown, format: BarValueFormat): string {
  const n = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(n)) return "—";
  if (format === "percent") return `${n.toFixed(1)}%`;
  return n.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

/** Custom tooltip: series sorted by value descending (largest first). */
function SortedBarTooltip({
  active,
  payload,
  label,
  valueFormat,
}: {
  active?: boolean;
  payload?: TooltipRow[];
  label?: string | number;
  valueFormat: BarValueFormat;
}) {
  if (!active || !payload?.length) return null;
  const rows = [...payload]
    .filter((p) => p != null && p.value != null && !Number.isNaN(Number(p.value)))
    .sort((a, b) => Number(b.value) - Number(a.value));
  if (!rows.length) return null;

  return (
    <div
      className="rounded-md border border-border px-2.5 py-2 text-xs shadow-md"
      style={{ background: "var(--popover)", color: "var(--popover-foreground)" }}
    >
      <p className="mb-1.5 font-medium">{label}</p>
      <ul className="flex flex-col gap-1">
        {rows.map((row) => (
          <li key={String(row.dataKey)} className="flex items-center justify-between gap-4">
            <span className="flex items-center gap-1.5">
              <span
                className="inline-block size-2.5 shrink-0 rounded-sm"
                style={{ background: String(row.color ?? row.fill ?? "#888") }}
              />
              <span>{row.name}</span>
            </span>
            <span className="tabular-nums font-medium">
              {formatTooltipValue(row.value, valueFormat)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function signedFill(value: unknown, colors: SignedValueColors): string {
  const n = Number(value);
  if (Number.isNaN(n) || n === 0) return colors.zero ?? colors.positive;
  return n > 0 ? colors.positive : colors.negative;
}

export function BarChartCard({
  title,
  data,
  xKey,
  series,
  goal,
  goalLabel,
  height = 260,
  stacked = false,
  subtitle,
  valueFormat = "dollars",
  headerRight,
  signedValueColors,
}: {
  title: string;
  data: Record<string, unknown>[];
  xKey: string;
  series: Series[];
  goal?: number;
  goalLabel?: string;
  height?: number;
  stacked?: boolean;
  subtitle?: string;
  valueFormat?: BarValueFormat;
  headerRight?: ReactNode;
  /** When set, that series’ bars turn green/red from the signed value. */
  signedValueColors?: SignedValueColors;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2 space-y-0">
        <div className="flex flex-col gap-1">
          <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
          {subtitle ? (
            <p className="text-xs text-muted-foreground">{subtitle}</p>
          ) : null}
        </div>
        {headerRight ? <div className="shrink-0">{headerRight}</div> : null}
      </CardHeader>
      <CardContent>
        <ResponsiveContainer width="100%" height={height}>
          <BarChart data={data} margin={{ top: 8, right: 12, left: -4, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
            <XAxis dataKey={xKey} tick={{ fontSize: 12 }} />
            <YAxis
              tick={{ fontSize: 12 }}
              tickFormatter={(v) => formatTick(Number(v), valueFormat)}
              width={52}
            />
            <Tooltip
              content={<SortedBarTooltip valueFormat={valueFormat} />}
              cursor={{ fill: "var(--muted)", opacity: 0.35 }}
            />
            {series.length > 1 ? (
              <Legend wrapperStyle={{ fontSize: 12 }} />
            ) : null}
            {series.map((s, i) => {
              const stackId =
                s.stackId !== undefined
                  ? s.stackId
                  : stacked
                    ? "stack"
                    : undefined;
              const useSigned = signedValueColors?.dataKey === s.key;
              return (
                <Bar
                  key={s.key}
                  dataKey={s.key}
                  name={s.label}
                  fill={s.color ?? chartColorAt(i)}
                  radius={stackId && i < series.length - 1 ? 0 : 2}
                  stackId={stackId}
                >
                  {useSigned
                    ? data.map((row, idx) => (
                        <Cell
                          key={`${s.key}-${idx}`}
                          fill={signedFill(row[s.key], signedValueColors)}
                        />
                      ))
                    : null}
                </Bar>
              );
            })}
            {goal != null ? (
              <ReferenceLine
                y={goal}
                stroke="var(--destructive)"
                strokeDasharray="4 4"
                label={{ value: goalLabel ?? "Goal", position: "insideTopRight", fontSize: 11 }}
              />
            ) : null}
          </BarChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}
