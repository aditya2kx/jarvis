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
}) {
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
          <LineChart data={data} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
            <XAxis dataKey={xKey} tick={{ fontSize: 12 }} />
            <YAxis tick={{ fontSize: 12 }} />
            <Tooltip
              contentStyle={{
                background: "var(--popover)",
                border: "1px solid var(--border)",
                fontSize: 12,
              }}
              labelFormatter={(label, payload) => {
                const priorBucket = payload?.[0]?.payload?.prior_bucket;
                if (typeof priorBucket === "string" && priorBucket) {
                  return `${label}  ·  prior ${priorBucket}`;
                }
                return String(label);
              }}
              formatter={(value, name) => {
                if (value == null || value === "") return ["—", name];
                const n = typeof value === "number" ? value : Number(value);
                if (Number.isNaN(n)) return ["—", name];
                return [n.toLocaleString(undefined, { maximumFractionDigits: 2 }), name];
              }}
            />
            {series.map((s, i) => (
              <Line
                key={s.key}
                type="monotone"
                dataKey={s.key}
                name={s.label}
                stroke={s.color ?? `var(--chart-${(i % 5) + 1})`}
                strokeWidth={2}
                strokeDasharray={s.dashed ? "6 4" : undefined}
                dot={false}
                // Gaps (null) must not draw a fake flat $0 line across empty history.
                connectNulls={false}
              />
            ))}
            {goal != null ? (
              <ReferenceLine
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
