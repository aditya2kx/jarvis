/**
 * Needed headcount for Staffing coverage (Issue #337).
 * need(t) = max(labor floor at t, ⌈typical orders for that weekday+hour ÷ orders per person⌉),
 * zero outside the labor-floor windows (store closed). Rounding up is deliberate:
 * when in doubt, staff the higher number.
 */

import { formatClockMin, type OccupancyPoint } from "@/lib/labor/coverage-model";

/** Typical (median) Payment orders for one weekday (0 = Mon … 6 = Sun) and local hour. */
export type DemandCell = { dow: number; hour: number; orders: number };

export type FloorWindow = { fromMin: number; toMin: number; min: number };

/** One person from the 6:30 opener until closing duties end at 8:30 PM. */
export const DEFAULT_LABOR_FLOOR: FloorWindow[] = [{ fromMin: 6 * 60 + 30, toMin: 20 * 60 + 30, min: 1 }];

export const DEFAULT_ORDERS_PER_PERSON = 4;

/** Mon = 0 … Sun = 6 for an ISO date, independent of the runtime timezone. */
export function isoWeekdayMon0(iso: string): number {
  const sun0 = new Date(`${iso.slice(0, 10)}T12:00:00Z`).getUTCDay();
  return (sun0 + 6) % 7;
}

export function floorAt(t: number, floor: FloorWindow[] = DEFAULT_LABOR_FLOOR): number | null {
  const w = floor.find((f) => f.fromMin <= t && t < f.toMin);
  return w ? w.min : null;
}

/** Needed headcount per occupancy step, aligned with `points` (same `min` values). */
export function needSeries(
  iso: string,
  points: Pick<OccupancyPoint, "min">[],
  demand: DemandCell[],
  ordersPerPerson: number = DEFAULT_ORDERS_PER_PERSON,
  floor: FloorWindow[] = DEFAULT_LABOR_FLOOR,
): number[] {
  const dow = isoWeekdayMon0(iso);
  const byHour = new Map<number, number>();
  for (const c of demand) if (c.dow === dow) byHour.set(c.hour, c.orders);
  const opp = ordersPerPerson > 0 ? ordersPerPerson : DEFAULT_ORDERS_PER_PERSON;
  return points.map(({ min }) => {
    const f = floorAt(min, floor);
    if (f == null) return 0;
    const orders = byHour.get(Math.floor(min / 60)) ?? 0;
    return Math.max(f, Math.ceil(orders / opp));
  });
}

/** Headcount covering a step: clocked if punched, else scheduled, plus ADP open slots. */
export function onFloor(p: Pick<OccupancyPoint, "actual" | "scheduled"> & { open?: number }): number {
  return (p.actual > 0 ? p.actual : p.scheduled) + (p.open ?? 0);
}

export type ShortWindow = { fromMin: number; toMin: number; short: number };

/** Contiguous windows where on-floor < need, merged when the shortfall is equal. */
export function shortWindows(points: OccupancyPoint[], need: number[], stepMin = 15): ShortWindow[] {
  const out: ShortWindow[] = [];
  points.forEach((p, i) => {
    const short = Math.max(0, (need[i] ?? 0) - onFloor(p));
    const last = out[out.length - 1];
    if (short > 0 && last && last.short === short && last.toMin === p.min) {
      last.toMin = p.min + stepMin;
    } else if (short > 0) {
      out.push({ fromMin: p.min, toMin: p.min + stepMin, short });
    }
  });
  return out;
}

/** Person-hours missing vs need (Σ shortfall × step). */
export function shortPersonHours(points: OccupancyPoint[], need: number[], stepMin = 15): number {
  return points.reduce((a, p, i) => a + Math.max(0, (need[i] ?? 0) - onFloor(p)) * (stepMin / 60), 0);
}

export function shortNarrative(windows: ShortWindow[]): string {
  if (!windows.length) return "Meets the needed headcount all day.";
  return `Short: ${windows
    .map((w) => `${formatClockMin(w.fromMin)}–${formatClockMin(w.toMin)} (−${w.short})`)
    .join(" · ")}`;
}
