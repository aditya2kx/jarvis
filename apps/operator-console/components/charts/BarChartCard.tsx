"use client";

import { useId, useState, type ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Series } from "./LineChartCard";
import { LABOR_CHART_COLORS, seriesColorAt } from "@/lib/charts/palette";
import { useIsPhone } from "@/components/shell/useIsPhone";
import {
  FloatingReadout,
  ReadoutSlot,
  ScrubReadout,
  SeriesToggles,
  axisLabel,
  useHiddenSeries,
  type Readout,
  type ReadoutRow,
} from "./ChartInteraction";

export type BarValueFormat = "dollars" | "percent" | "number";

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
  if (format === "number") {
    const n = Number(value);
    if (Math.abs(n) >= 1000) return `${(n / 1000).toFixed(n % 1000 === 0 ? 0 : 1)}k`;
    return Number.isInteger(n) ? `${n}` : n.toFixed(1);
  }
  const n = Number(value);
  const sign = n < 0 ? "−" : "";
  const abs = Math.abs(n);
  if (abs >= 1000) return `${sign}$${(abs / 1000).toFixed(abs % 1000 === 0 ? 0 : 1)}k`;
  return `${sign}$${abs.toFixed(0)}`;
}

function formatTooltipValue(value: unknown, format: BarValueFormat): string {
  const n = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(n)) return "—";
  if (format === "percent") return `${n.toFixed(1)}%`;
  if (format === "number") return n.toLocaleString("en-US");
  return n.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

function isEntryList(v: unknown): v is { label: string; value: string; color?: string }[] {
  return (
    Array.isArray(v) &&
    v.length > 0 &&
    v.every(
      (e) =>
        e != null &&
        typeof e === "object" &&
        typeof (e as { label?: unknown }).label === "string" &&
        typeof (e as { value?: unknown }).value === "string",
    )
  );
}

/** Readout for one bar group: visible series by value, largest first. A row's
 *  `tooltipEntries` replace the series list (fixed order; values pre-formatted) and
 *  `tooltipLines` become footnotes. */
function barReadout(
  label: unknown,
  row: Record<string, unknown> | undefined,
  visible: Series[],
  colorOf: (s: Series, row: Record<string, unknown>) => string,
  valueFormat: BarValueFormat,
): Readout | null {
  if (!row) return null;
  const notes = Array.isArray(row.tooltipLines)
    ? row.tooltipLines.filter((line): line is string => typeof line === "string" && line.length > 0)
    : [];
  const rows: ReadoutRow[] = isEntryList(row.tooltipEntries)
    ? row.tooltipEntries
    : visible
        .filter((s) => row[s.key] != null && !Number.isNaN(Number(row[s.key])))
        .sort((a, b) => Number(row[b.key]) - Number(row[a.key]))
        .map((s) => ({
          label: s.label,
          value: formatTooltipValue(row[s.key], valueFormat),
          color: colorOf(s, row),
        }));
  if (!rows.length && !notes.length) return null;
  return { title: axisLabel(label), rows, notes };
}

function signedFill(value: unknown, colors: SignedValueColors): string {
  const n = Number(value);
  if (Number.isNaN(n) || n === 0) return colors.zero ?? colors.positive;
  return n > 0 ? colors.positive : colors.negative;
}

/** Two-line axis tick when `payload.value` contains `\n` (e.g. `Jul 3\\nMo`). */
function MultilineXTick({
  x = 0,
  y = 0,
  payload,
}: {
  x?: number;
  y?: number;
  payload?: { value?: string | number };
}) {
  const lines = String(payload?.value ?? "").split("\n");
  return (
    <text x={x} y={y} textAnchor="middle" className="fill-muted-foreground">
      {lines.map((line, i) => (
        <tspan
          key={`${line}-${i}`}
          x={x}
          dy={i === 0 ? 12 : 11}
          fontSize={i === 0 ? 11 : 10}
          fontWeight={i === 0 ? 400 : 500}
        >
          {line}
        </tspan>
      ))}
    </text>
  );
}

export function BarChartCard({
  title,
  data,
  xKey,
  series,
  goal,
  goalLabel,
  goalStroke = LABOR_CHART_COLORS.goalLine,
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
  /** Reference-line stroke — default destructive; Labor uses gold (not red bars). */
  goalStroke?: string;
  height?: number;
  stacked?: boolean;
  subtitle?: string;
  valueFormat?: BarValueFormat;
  headerRight?: ReactNode;
  /** When set, that series’ bars turn green/red from the signed value. */
  signedValueColors?: SignedValueColors;
}) {
  const multilineX = data.some((row) => String(row[xKey] ?? "").includes("\n"));
  // Every multi-line day tick fits on desktop; at 390px a month of them overlaps.
  const phone = useIsPhone();
  // SVG url(#id) breaks on useId's colons; one chart can render twice per page.
  const patternPrefix = `hatch-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const hatched = series.filter((s) => s.pattern === "hatch");
  const { hidden, toggle } = useHiddenSeries();
  const [readoutEl, setReadoutEl] = useState<HTMLDivElement | null>(null);
  const baseColor = (s: Series) => s.color ?? seriesColorAt(series.indexOf(s));
  const colorOf = (s: Series, row: Record<string, unknown>) =>
    signedValueColors?.dataKey === s.key ? signedFill(row[s.key], signedValueColors) : baseColor(s);
  const visible = series.filter((s) => !hidden.has(s.key));
  const readoutFor = (label: unknown, row: Record<string, unknown> | undefined) =>
    barReadout(label, row, visible, colorOf, valueFormat);
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2 space-y-0">
        <div className="flex flex-col gap-1">
          <CardTitle className="text-sm font-semibold">{title}</CardTitle>
          {subtitle ? (
            <p className="text-xs text-muted-foreground">{subtitle}</p>
          ) : null}
        </div>
        {headerRight ? <div className="shrink-0">{headerRight}</div> : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <SeriesToggles
          items={series.map((s) => ({ key: s.key, label: s.label, color: baseColor(s) }))}
          hidden={hidden}
          onToggle={toggle}
        />
        {phone ? <ReadoutSlot onMount={setReadoutEl} /> : null}
        <ResponsiveContainer width="100%" height={height} className="touch-pan-y">
          <BarChart
            data={data}
            margin={{ top: 8, right: 12, left: -4, bottom: multilineX ? 8 : 0 }}
          >
            {hatched.length ? (
              <defs>
                {hatched.map((s) => {
                  const color = s.color ?? seriesColorAt(series.indexOf(s));
                  return (
                    <pattern
                      key={s.key}
                      id={`${patternPrefix}-${s.key}`}
                      width={6}
                      height={6}
                      patternUnits="userSpaceOnUse"
                      patternTransform="rotate(45)"
                    >
                      <rect width={6} height={6} fill={color} fillOpacity={0.18} />
                      <line x1={0} y1={0} x2={0} y2={6} stroke={color} strokeWidth={2.5} />
                    </pattern>
                  );
                })}
              </defs>
            ) : null}
            <CartesianGrid vertical={false} className="stroke-border/70" />
            <XAxis
              dataKey={xKey}
              tick={multilineX ? <MultilineXTick /> : { fontSize: 12 }}
              tickLine={false}
              axisLine={false}
              height={multilineX ? 36 : undefined}
              interval={multilineX ? (phone ? "preserveStartEnd" : 0) : undefined}
              minTickGap={multilineX ? (phone ? 6 : 0) : undefined}
            />
            <YAxis
              tick={{ fontSize: 12 }}
              tickFormatter={(v) => formatTick(Number(v), valueFormat)}
              width={52}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip
              content={({ active, label, payload }) =>
                phone || !active ? null : (
                  <FloatingReadout
                    readout={readoutFor(label, payload?.[0]?.payload as Record<string, unknown> | undefined)}
                  />
                )
              }
              cursor={{ fill: "var(--muted)", opacity: phone ? 0.6 : 0.35 }}
            />
            {phone ? (
              <ScrubReadout
                target={readoutEl}
                build={(label) => readoutFor(label, data.find((row) => String(row[xKey] ?? "") === label))}
              />
            ) : null}
            {series.map((s, i) => {
              const stackId =
                s.stackId !== undefined
                  ? s.stackId
                  : stacked
                    ? "stack"
                    : undefined;
              const useSigned = signedValueColors?.dataKey === s.key;
              const color = baseColor(s);
              const hatch = s.pattern === "hatch";
              const outline = s.pattern === "outline";
              return (
                <Bar
                  key={s.key}
                  dataKey={s.key}
                  name={s.label}
                  hide={hidden.has(s.key)}
                  fill={hatch ? `url(#${patternPrefix}-${s.key})` : color}
                  fillOpacity={outline ? 0.15 : undefined}
                  stroke={hatch || outline ? color : undefined}
                  strokeWidth={hatch ? 1 : outline ? 1.5 : undefined}
                  strokeDasharray={hatch ? "3 2" : outline ? "4 3" : undefined}
                  radius={stackId && i < series.length - 1 ? 0 : [3, 3, 0, 0]}
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
                stroke={goalStroke}
                strokeDasharray="4 4"
                strokeWidth={1.5}
                label={{
                  value: goalLabel ?? "Goal",
                  position: "insideTopRight",
                  fontSize: 11,
                  fill: goalStroke,
                }}
              />
            ) : null}
          </BarChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}
