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
}

// Dashed goal line: the only "computed" thing here is a visual comparison of
// already-fetched rows against a goal value — no new metric math (see
// EXECUTION.md §4 M2 step 4 — this is components/charts/GoalLine.tsx inlined
// as a prop since Recharts ReferenceLine needs no extra component).
export function LineChartCard({
  title,
  data,
  xKey,
  series,
  goal,
  goalLabel,
  height = 260,
}: {
  title: string;
  data: Record<string, unknown>[];
  xKey: string;
  series: Series[];
  goal?: number;
  goalLabel?: string;
  height?: number;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
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
                connectNulls
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
