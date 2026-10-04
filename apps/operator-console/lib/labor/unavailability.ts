/**
 * ADP unavailability → per-day blocked windows (Issue #337). Pending requests
 * count as much as approved ones: ADP only paints a block after approval, but a
 * pending request is still what the employee told us.
 */

import { isoWeekdayMon0 } from "@/lib/labor/staffing-need";
import { timeToMin } from "@/lib/labor/schedule-inputs";
import { parseShiftRangesJson } from "@/lib/labor/shift-ranges";

export type UnavailabilityInput = {
  employee: string;
  status: "pending" | "approved";
  first_date: string;
  from_time: string | null;
  to_time: string | null;
  all_day: boolean;
  repeat_weekday: number | null;
  repeat_until: string | null;
};

export type Block = { fromMin: number; toMin: number; status: "pending" | "approved" };

const DAY: [number, number] = [0, 24 * 60];

/** Does this row cover `iso` (one-off date, or a weekly repeat through repeat_until)? */
export function coversDate(row: UnavailabilityInput, iso: string): boolean {
  const day = iso.slice(0, 10);
  if (day === row.first_date) return true;
  return (
    row.repeat_weekday != null &&
    day > row.first_date &&
    (!row.repeat_until || day <= row.repeat_until) &&
    isoWeekdayMon0(day) === row.repeat_weekday
  );
}

function window(row: UnavailabilityInput): [number, number] {
  if (row.all_day) return DAY;
  const s = timeToMin(row.from_time ?? "");
  const e = timeToMin(row.to_time ?? "");
  if (s == null || e == null) return DAY;
  return [s, e > s ? e : 24 * 60];
}

/** Blocked windows per employee on `iso`. */
export function blocksOn(rows: UnavailabilityInput[], iso: string): Map<string, Block[]> {
  const out = new Map<string, Block[]>();
  for (const r of rows) {
    if (!coversDate(r, iso)) continue;
    const [fromMin, toMin] = window(r);
    out.set(r.employee, [...(out.get(r.employee) ?? []), { fromMin, toMin, status: r.status }]);
  }
  return out;
}

/** Longest part of [s, e) that avoids every block. */
export function freeSegment(s: number, e: number, blocks: readonly Block[] = []): [number, number] {
  let segs: [number, number][] = [[s, e]];
  for (const b of blocks) {
    segs = segs.flatMap(([a, z]): [number, number][] =>
      b.toMin <= a || b.fromMin >= z ? [[a, z]] : [
        ...(b.fromMin > a ? [[a, b.fromMin] as [number, number]] : []),
        ...(b.toMin < z ? [[b.toMin, z] as [number, number]] : []),
      ],
    );
  }
  return segs.reduce<[number, number]>((best, x) => (x[1] - x[0] > best[1] - best[0] ? x : best), [s, s]);
}

export type ScheduledShift = { date: string; employee: string; shift_ranges_json: string | null };

export type Conflict = {
  date: string;
  employee: string;
  shift: { startMin: number; endMin: number };
  block: Block;
};

/** Scheduled ADP shifts that overlap the same person's unavailability. */
export function scheduleConflicts(rows: UnavailabilityInput[], shifts: ScheduledShift[]): Conflict[] {
  const out: Conflict[] = [];
  const byDate = new Map<string, Map<string, Block[]>>();
  for (const s of shifts) {
    const blocks = (byDate.get(s.date) ?? byDate.set(s.date, blocksOn(rows, s.date)).get(s.date)!).get(s.employee);
    if (!blocks) continue;
    for (const r of parseShiftRangesJson(s.shift_ranges_json)) {
      const hit = blocks.find((b) => overlapMinutes(r.startMin, r.endMin, [b]) > 0);
      if (hit) out.push({ date: s.date, employee: s.employee, shift: { startMin: r.startMin, endMin: r.endMin }, block: hit });
    }
  }
  return out.sort((a, b) => a.date.localeCompare(b.date) || a.employee.localeCompare(b.employee));
}

export type PersonAvailability = {
  employee: string;
  pending: number;
  approved: number;
  upcomingShifts: number;
  conflicts: number;
};

/** One row per person on the schedule or with unavailability on file. */
export function availabilityByPerson(
  rows: UnavailabilityInput[],
  shifts: ScheduledShift[],
  conflicts: Conflict[],
): PersonAvailability[] {
  const m = new Map<string, PersonAvailability>();
  const get = (employee: string) =>
    m.get(employee) ?? m.set(employee, { employee, pending: 0, approved: 0, upcomingShifts: 0, conflicts: 0 }).get(employee)!;
  for (const r of rows) get(r.employee)[r.status] += 1;
  for (const s of shifts) get(s.employee).upcomingShifts += 1;
  for (const c of conflicts) get(c.employee).conflicts += 1;
  return [...m.values()].sort(
    (a, b) => b.conflicts - a.conflicts || b.pending - a.pending || a.employee.localeCompare(b.employee),
  );
}

/** Minutes of overlap between [s, e) and the blocks. */
export function overlapMinutes(s: number, e: number, blocks: readonly Block[]): number {
  return blocks.reduce((sum, b) => sum + Math.max(0, Math.min(e, b.toMin) - Math.max(s, b.fromMin)), 0);
}
