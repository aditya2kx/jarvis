"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Series } from "./LineChartCard";

export function BarChartCard({
  title,
  data,
  xKey,
  series,
  goal,
  goalLabel,
  height = 260,
  stacked = false,
}: {
  title: string;
  data: Record<string, unknown>[];
  xKey: string;
  series: Series[];
  goal?: number;
  goalLabel?: string;
  height?: number;
  stacked?: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <ResponsiveContainer width="100%" height={height}>
          <BarChart data={data} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
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
              <Bar
                key={s.key}
                dataKey={s.key}
                name={s.label}
                fill={s.color ?? `var(--chart-${(i % 5) + 1})`}
                radius={2}
                stackId={stacked ? "stack" : undefined}
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
          </BarChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}
