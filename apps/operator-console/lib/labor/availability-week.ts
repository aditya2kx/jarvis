/**
 * Team week grid for the Availability page (Issue #427): one row per person,
 * one cell per day, holding that day's unavailability entries and ADP shifts.
 */

import { isoWeekdayMon0 } from "@/lib/labor/staffing-need";
import { parseShiftRangesJson } from "@/lib/labor/shift-ranges";
import { blockWindow, coversDate, type ScheduledShift, type UnavailabilityInput } from "@/lib/labor/unavailability";

export type CellEntry<T> = { row: T; fromMin: number; toMin: number; clash: boolean };
export type CellShift = { startMin: number; endMin: number; clash: boolean };
export type WeekCell<T> = { date: string; entries: CellEntry<T>[]; shifts: CellShift[] };
export type WeekRow<T> = { employee: string; days: WeekCell<T>[] };

const DAY_MS = 86_400_000;

export function addDays(iso: string, n: number): string {
  return new Date(Date.parse(`${iso.slice(0, 10)}T12:00:00Z`) + n * DAY_MS).toISOString().slice(0, 10);
}

/** Monday of the week holding `iso`. */
export function mondayOf(iso: string): string {
  return addDays(iso, -isoWeekdayMon0(iso));
}

export function weekDates(monday: string): string[] {
  return Array.from({ length: 7 }, (_, i) => addDays(monday, i));
}

const overlaps = (a0: number, a1: number, b0: number, b1: number) => a0 < b1 && b0 < a1;

/**
 * Rows for `people` (in the order given) across the week starting `monday`.
 * An entry and a shift clash when the same person's windows overlap that day.
 */
export function weekGrid<T extends UnavailabilityInput>(
  rows: T[],
  shifts: ScheduledShift[],
  people: string[],
  monday: string,
): WeekRow<T>[] {
  const dates = weekDates(monday);
  const shiftsBy = new Map<string, { startMin: number; endMin: number }[]>();
  for (const s of shifts) {
    if (!dates.includes(s.date)) continue;
    const k = `${s.employee}|${s.date}`;
    shiftsBy.set(k, [...(shiftsBy.get(k) ?? []), ...parseShiftRangesJson(s.shift_ranges_json)]);
  }
  return people.map((employee) => ({
    employee,
    days: dates.map((date) => {
      const day = shiftsBy.get(`${employee}|${date}`) ?? [];
      const entries = rows
        .filter((r) => r.employee === employee && coversDate(r, date))
        .map((row) => {
          const [fromMin, toMin] = blockWindow(row);
          return { row, fromMin, toMin, clash: day.some((s) => overlaps(s.startMin, s.endMin, fromMin, toMin)) };
        })
        .sort((a, b) => a.fromMin - b.fromMin);
      return {
        date,
        entries,
        shifts: day.map((s) => ({
          ...s,
          clash: entries.some((e) => overlaps(s.startMin, s.endMin, e.fromMin, e.toMin)),
        })),
      };
    }),
  }));
}
