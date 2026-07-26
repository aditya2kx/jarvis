import type { Series } from "@/components/charts/LineChartCard";

export type SalesMetric = "net_sales" | "orders" | "items_sold";

export interface SalesPivotRow {
  date: string;
  source: string | null;
  net_sales: number;
  orders: number;
  items_sold: number;
}

/**
 * Pivot long sales rows into Recharts-friendly wide rows.
 * Aggregate mode: one series key = metric. Breakdown: one series per source.
 */
export function pivotSalesChart(
  rows: SalesPivotRow[],
  metric: SalesMetric,
  breakdown: boolean,
  metricLabel: string,
): { data: Record<string, unknown>[]; series: Series[] } {
  if (!breakdown) {
    const byDate = new Map<string, number>();
    for (const r of rows) {
      byDate.set(r.date, (byDate.get(r.date) ?? 0) + Number(r[metric] ?? 0));
    }
    const data = Array.from(byDate.entries()).map(([date, value]) => ({
      date,
      [metric]: value,
    }));
    return { data, series: [{ key: metric, label: metricLabel }] };
  }

  const byDate = new Map<string, Record<string, unknown>>();
  const sourceKeys = new Set<string>();
  for (const r of rows) {
    const src = r.source?.trim() || "(unknown)";
    sourceKeys.add(src);
    const entry = byDate.get(r.date) ?? { date: r.date };
    entry[src] = (Number(entry[src]) || 0) + Number(r[metric] ?? 0);
    byDate.set(r.date, entry);
  }
  const series = Array.from(sourceKeys)
    .sort((a, b) => a.localeCompare(b))
    .map((k) => ({ key: k, label: k }));
  return { data: Array.from(byDate.values()), series };
}

/**
 * Align prior-period aggregate pivot onto current bucket labels by index
 * (same grain length). Prior values land under `prior_<metricKey>` with a
 * dashed series — used by Trend + Compare prior.
 */
export function mergePriorSeries(
  current: { data: Record<string, unknown>[]; series: Series[] },
  prior: { data: Record<string, unknown>[]; series: Series[] },
  metricKey: string,
  priorLabel = "Prior period",
): { data: Record<string, unknown>[]; series: Series[] } {
  const priorKey = `prior_${metricKey}`;
  const priorByIndex = prior.data.map((row) => Number(row[metricKey] ?? 0));
  const data = current.data.map((row, i) => ({
    ...row,
    [priorKey]: priorByIndex[i] ?? null,
  }));
  const series: Series[] = [
    ...current.series,
    { key: priorKey, label: priorLabel, dashed: true },
  ];
  return { data, series };
}
