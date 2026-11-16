/**
 * Recent-volume trend for the staffing forecast (Issue #512). The hourly pattern stays the
 * 8-week median (`laborDemandProfile`); this only scales its level by how busy the last few
 * weeks were, most recent weighted highest, so a slow month shows up within a week or two
 * without one odd day reshaping a weekday.
 */

import { shiftCalendarDate } from "@/lib/filters/range";
import { isoWeekdayMon0, type DemandCell } from "@/lib/labor/staffing-need";

/** One store day from `vw_labor_daily_live`. */
export type TrendDay = { date: string; orders: number; net_sales: number; labor_cost: number; hours: number };

const BASE_DAYS = 56;

/** Days in [asOf − from, asOf − to) that had orders — a closed day isn't a slow day. */
function openDays(days: TrendDay[], asOfIso: string, fromDays: number, toDays: number): TrendDay[] {
  const start = shiftCalendarDate(asOfIso, "day", -fromDays);
  const end = shiftCalendarDate(asOfIso, "day", -toDays);
  return days.filter((d) => d.date >= start && d.date < end && d.orders > 0);
}

const perDay = (days: TrendDay[]) => (days.length ? days.reduce((a, d) => a + d.orders, 0) / days.length : null);

/**
 * Weighted orders-per-open-day of the last `weights.length` weeks ÷ the 8-week average.
 * Weeks with no open days drop out; 1 when there is nothing to compare.
 */
export function trendFactor(days: TrendDay[], asOfIso: string, weights: number[] | null): number {
  if (!weights?.length) return 1;
  const base = perDay(openDays(days, asOfIso, BASE_DAYS, 0));
  if (!base) return 1;
  let sum = 0;
  let wsum = 0;
  weights.forEach((w, k) => {
    const v = perDay(openDays(days, asOfIso, 7 * (k + 1), 7 * k));
    if (v == null || !(w > 0)) return;
    sum += v * w;
    wsum += w;
  });
  return wsum > 0 ? sum / wsum / base : 1;
}

export function scaleDemand(demand: DemandCell[], factor: number): DemandCell[] {
  return factor === 1 ? demand : demand.map((c) => ({ ...c, orders: c.orders * factor }));
}

/**
 * Next week's net sales (8-week average per weekday × trend) and labor cost per paid hour
 * over the last 4 weeks — the two inputs of the labor % cap. Nulls when there is no history.
 */
export function forecastWeek(
  days: TrendDay[],
  asOfIso: string,
  factor: number,
): { sales: number | null; costPerHour: number | null } {
  const base = openDays(days, asOfIso, BASE_DAYS, 0);
  const byDow = new Map<number, number[]>();
  for (const d of base) byDow.set(isoWeekdayMon0(d.date), [...(byDow.get(isoWeekdayMon0(d.date)) ?? []), d.net_sales]);
  const sales = byDow.size
    ? [...byDow.values()].reduce((a, v) => a + v.reduce((x, y) => x + y, 0) / v.length, 0) * factor
    : null;
  const recent = openDays(days, asOfIso, 28, 0);
  const hours = recent.reduce((a, d) => a + d.hours, 0);
  const cost = recent.reduce((a, d) => a + d.labor_cost, 0);
  return { sales, costPerHour: hours > 0 && cost > 0 ? cost / hours : null };
}

/** The week's ceiling on drafted hours: the labor % cap when set and forecastable, else the hours goal. */
export type WeekCap =
  | { kind: "labor"; hours: number; pct: number; sales: number }
  | { kind: "goal"; hours: number };

export function weekCap(
  laborCapPct: number | null,
  forecast: { sales: number | null; costPerHour: number | null },
  goalHoursWeek?: number,
): WeekCap | null {
  if (laborCapPct != null && forecast.sales && forecast.costPerHour) {
    return {
      kind: "labor",
      hours: (laborCapPct * forecast.sales) / forecast.costPerHour,
      pct: laborCapPct,
      sales: forecast.sales,
    };
  }
  return goalHoursWeek != null ? { kind: "goal", hours: goalHoursWeek } : null;
}

/** "200h cap · 40% of $8.2k forecast sales" or "210h goal". */
export function capLabel(c: WeekCap): string {
  const h = `${Math.round(c.hours)}h`;
  return c.kind === "goal"
    ? `${h} goal`
    : `${h} cap · ${Math.round(c.pct * 100)}% of $${(c.sales / 1000).toFixed(1)}k forecast sales`;
}
