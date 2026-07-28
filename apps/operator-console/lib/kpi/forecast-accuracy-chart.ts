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
  scheduled_hours?: number | null;
};

export type GoalSchedulePoint = {
  date: string;
  forecast_items: number;
  scheduled_hours: number | null;
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
      // Day grain: same calendar day — prefer live forward (avoid double-count).
      // Week/month grain: accuracy only has completed days with actuals; forward
      // has today→; buckets overlap on the current period so SUM the halves.
      prev.forecast =
        grain === "day" ? forecast : Number(prev.forecast ?? 0) + forecast;
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

/**
 * Goal total hours vs scheduled chart (Issue #202):
 * - Scheduled hours are Period-scoped (like actuals — look-back).
 * - Goal hours (= forecast_items × goal rate) cover Period history and
 *   continue into the forward horizon.
 */
export function mergeGoalHoursChart(
  period: GoalSchedulePoint[],
  fwd: ForwardPoint[],
  opts: { goalHoursPerItem: number; grain: Grain },
): Record<string, unknown>[] {
  const { goalHoursPerItem, grain } = opts;
  const map = new Map<
    string,
    { date: string; goal_shift_hours: number | null; scheduled_hours: number | null }
  >();

  for (const r of period) {
    const key = String(r.date);
    const items = Number(r.forecast_items) || 0;
    map.set(key, {
      date: key,
      goal_shift_hours: Number((items * goalHoursPerItem).toFixed(1)),
      scheduled_hours: r.scheduled_hours == null ? null : Number(r.scheduled_hours),
    });
  }

  for (const r of fwd) {
    const key = String(r.date);
    const items = Number(r.forecast_items) || 0;
    const goal = Number((items * goalHoursPerItem).toFixed(1));
    const prev = map.get(key);
    if (prev) {
      // Period query already covers all Period days in this bucket (including
      // future days still inside the Period). Replacing with forward would
      // drop elapsed days at week/month grain; summing would double-count.
      // Keep Period goal; only append buckets beyond the Period window.
    } else {
      map.set(key, {
        date: key,
        goal_shift_hours: goal,
        scheduled_hours: null,
      });
    }
  }

  return [...map.values()]
    .sort((a, b) => (dateSortKey(a.date) > dateSortKey(b.date) ? 1 : -1))
    .map((r) => ({
      date: formatBucket(r.date, grain),
      goal_shift_hours: r.goal_shift_hours,
      scheduled_hours: r.scheduled_hours,
    }));
}

/**
 * Mean absolute percentage error over forecast-vs-actual rows.
 * Skips rows with no actual (forward-only points) — cannot divide by zero
 * and must not dilute MAPE with Period-independent look-ahead.
 */
export function mapeForecastAccuracy(
  rows: { forecast: number; actual: number }[],
): number | undefined {
  const usable = rows.filter((r) => r.actual);
  if (!usable.length) return undefined;
  const sum = usable.reduce((s, r) => s + Math.abs(r.actual - r.forecast) / r.actual, 0);
  return (sum / usable.length) * 100;
}
