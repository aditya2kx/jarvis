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
 * Zero-fill missing grain buckets so sparse BQ rows still align for Compare.
 * `rows` may use raw ISO `date` values; returned dates stay ISO (caller formats).
 */
export function fillSalesSpine(
  rows: SalesPivotRow[],
  bucketStarts: string[],
): SalesPivotRow[] {
  const byDate = new Map<string, SalesPivotRow>();
  for (const r of rows) {
    const key = String(r.date).slice(0, 10);
    const cur = byDate.get(key);
    if (!cur) {
      byDate.set(key, {
        date: key,
        source: r.source,
        net_sales: Number(r.net_sales) || 0,
        orders: Number(r.orders) || 0,
        items_sold: Number(r.items_sold) || 0,
      });
    } else {
      // Aggregate mode may already be one row per date; breakdown keeps source.
      if (r.source && cur.source && r.source !== cur.source) {
        // Leave breakdown rows as-is — spine fill only for aggregate compare.
        continue;
      }
      cur.net_sales += Number(r.net_sales) || 0;
      cur.orders += Number(r.orders) || 0;
      cur.items_sold += Number(r.items_sold) || 0;
    }
  }
  return bucketStarts.map(
    (date) =>
      byDate.get(date) ?? {
        date,
        source: null,
        net_sales: 0,
        orders: 0,
        items_sold: 0,
      },
  );
}

/**
 * Align prior-period aggregate pivot onto current bucket labels by index
 * (same grain length after priorWindow(win, grain) + spine fill). Prior values
 * land under `prior_<metricKey>` with a dashed series — used by Trend + Compare.
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
    [priorKey]: i < priorByIndex.length ? priorByIndex[i]! : null,
  }));
  const series: Series[] = [
    ...current.series,
    { key: priorKey, label: priorLabel, dashed: true },
  ];
  return { data, series };
}
