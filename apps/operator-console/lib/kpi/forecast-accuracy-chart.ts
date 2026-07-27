import { dateSortKey } from "@/lib/format";
import { formatBucket, type Grain } from "@/lib/filters/range";

export type AccuracyPoint = {
  date: string;
  forecast_orders: number;
  actual_orders: number;
  forecast_items: number;
  actual_items: number;
};

export type ForwardPoint = {
  date: string;
  forecast_orders: number;
  forecast_items: number;
};

/**
 * Build the Forecast accuracy chart series (Issue #202 follow-on):
 * - Actual is Period-scoped (only points from `acc`).
 * - Forecast covers Period history *and* continues today→horizon from `fwd`
 *   so "This month" / "This week" never clips the look-ahead.
 * - Both series share the same Aggregation grain (callers query both at `grain`).
 * MAPE should still use `acc` alone (dates with actuals), not forward-only points.
 */
export function mergeForecastAccuracyChart(
  acc: AccuracyPoint[],
  fwd: ForwardPoint[],
  opts: { forecastKey: "forecast_orders" | "forecast_items"; actualKey: "actual_orders" | "actual_items"; grain: Grain },
): Record<string, unknown>[] {
  const { forecastKey, actualKey, grain } = opts;
  const map = new Map<string, { date: string; forecast: number | null; actual: number | null }>();

  for (const r of acc) {
    const key = String(r.date);
    map.set(key, {
      date: key,
      forecast: Number(r[forecastKey]),
      actual: Number(r[actualKey]),
    });
  }

  for (const r of fwd) {
    const key = String(r.date);
    const prev = map.get(key);
    const forecast = Number(r[forecastKey]);
    if (prev) {
      // Prefer live forward row when Period overlaps today.
      prev.forecast = forecast;
    } else {
      map.set(key, { date: key, forecast, actual: null });
    }
  }

  return [...map.values()]
    .sort((a, b) => (dateSortKey(a.date) > dateSortKey(b.date) ? 1 : -1))
    .map((r) => ({
      date: formatBucket(r.date, grain),
      forecast: r.forecast,
      actual: r.actual,
    }));
}
