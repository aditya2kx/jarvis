"use client";

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

export interface Series {
  key: string;
  label: string;
  color?: string;
  dashed?: boolean;
  /** When set, overrides BarChartCard `stacked` for this series (Recharts stackId). */
  stackId?: string;
  /** Dual-axis: absolute series stay on left; % change uses right. */
  yAxisId?: "left" | "right";
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
  if (Math.abs(n) >= 1000) return `$${(n / 1000).toFixed(n % 1000 === 0 ? 0 : 1)}k`;
  return `$${n.toFixed(0)}`;
}

function formatPct(value: unknown): string {
  if (value == null || value === "") return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(n)) return "—";
  const rounded = Math.round(n * 10) / 10;
  const sign = rounded > 0 ? "+" : "";
  return `${sign}${rounded.toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
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

  return (
    <Card>
      <CardHeader className="gap-1">
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
        {subtitle ? (
          <p className="text-xs text-muted-foreground/80">{subtitle}</p>
        ) : null}
      </CardHeader>
      <CardContent>
        <ResponsiveContainer width="100%" height={height}>
          <LineChart
            data={data}
            margin={{ top: 8, right: hasRight ? 8 : 12, left: -12, bottom: 0 }}
          >
            <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
            <XAxis dataKey={xKey} tick={{ fontSize: 12 }} />
            <YAxis
              yAxisId="left"
              tick={{ fontSize: 12 }}
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
                tickFormatter={(v) => `${v}%`}
                width={48}
              />
            ) : null}
            <Tooltip
              // Compare keeps % change in row data (pct_*) without a third line;
              // append it to the tooltip under the current/prior absolute values.
              content={(props) => {
                const { active, payload, label } = props;
                if (!active || !payload?.length) return null;
                const row = payload[0]?.payload as Record<string, unknown> | undefined;
                const priorBucket = row?.prior_bucket;
                const pctEntry = row
                  ? Object.entries(row).find(([k, v]) => k.startsWith("pct_") && v != null)
                  : undefined;
                const header =
                  typeof priorBucket === "string" && priorBucket
                    ? `${label}  ·  vs ${priorBucket}`
                    : String(label ?? "");
                return (
                  <div
                    style={{
                      background: "var(--popover)",
                      border: "1px solid var(--border)",
                      fontSize: 12,
                      padding: "8px 10px",
                      borderRadius: 6,
                    }}
                  >
                    <div style={{ marginBottom: 4, fontWeight: 500 }}>{header}</div>
                    {payload.map((item) => {
                      const key = String(item.dataKey ?? "");
                      if (key.startsWith("pct_")) return null;
                      return (
                        <div key={key} style={{ color: item.color }}>
                          {String(item.name)}: {formatAbs(item.value, tipFormat)}
                        </div>
                      );
                    })}
                    {pctEntry ? (
                      <div style={{ marginTop: 4, color: "var(--muted-foreground)" }}>
                        % change: {formatPct(pctEntry[1])}
                      </div>
                    ) : null}
                  </div>
                );
              }}
            />
            {series.map((s, i) => (
              <Line
                key={s.key}
                yAxisId={s.yAxisId ?? "left"}
                type="monotone"
                dataKey={s.key}
                name={s.label}
                stroke={s.color ?? `var(--chart-${(i % 5) + 1})`}
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
                stroke="var(--destructive)"
                strokeDasharray="4 4"
                label={{ value: goalLabel ?? "Goal", position: "insideTopRight", fontSize: 11 }}
              />
            ) : null}
          </LineChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}
