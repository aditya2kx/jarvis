import type { Series } from "@/components/charts/LineChartCard";

export type SalesMetric = "net_sales" | "orders" | "items_sold";

export interface SalesPivotRow {
  date: string;
  source: string | null;
  net_sales: number;
  orders: number;
  items_sold: number;
}

// Re-export shared Trend/Compare helpers so existing sales imports keep working.
// New screens should import from `@/lib/charts/compare-series` directly.
export {
  compareGrainLabel,
  mergePriorSeries,
  pctChange,
  type PivotChart,
} from "@/lib/charts/compare-series";

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
    const byDate = new Map<string, number | null>();
    for (const r of rows) {
      const raw = r[metric] as number | null | undefined;
      if (raw == null) {
        if (!byDate.has(r.date)) byDate.set(r.date, null);
        continue;
      }
      const prev = byDate.get(r.date);
      byDate.set(r.date, (prev == null ? 0 : prev) + Number(raw));
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
 * Fill missing grain buckets so sparse BQ rows still align for Compare.
 * `missing: "zero"` (default) writes 0s — use for the current window.
 * `missing: "null"` leaves metric fields null so charts gap instead of faking $0
 * for weeks that never had transactions (e.g. pre-store history).
 *
 * Sales-shaped rows; other screens should add a domain-specific spine filler
 * (or a thin adapter into `{ date, value }`) rather than forking mergePriorSeries.
 */
export function fillSalesSpine(
  rows: SalesPivotRow[],
  bucketStarts: string[],
  missing: "zero" | "null" = "zero",
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
      if (r.source && cur.source && r.source !== cur.source) {
        continue;
      }
      cur.net_sales += Number(r.net_sales) || 0;
      cur.orders += Number(r.orders) || 0;
      cur.items_sold += Number(r.items_sold) || 0;
    }
  }
  return bucketStarts.map((date) => {
    const hit = byDate.get(date);
    if (hit) return hit;
    if (missing === "null") {
      return {
        date,
        source: null,
        net_sales: null as unknown as number,
        orders: null as unknown as number,
        items_sold: null as unknown as number,
      };
    }
    return {
      date,
      source: null,
      net_sales: 0,
      orders: 0,
      items_sold: 0,
    };
  });
}
