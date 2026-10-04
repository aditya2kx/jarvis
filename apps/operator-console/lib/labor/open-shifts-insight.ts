/**
 * Upcoming shifts nobody is on yet — ADP open shifts plus draft shifts no
 * available person could take — rolled up by weekday and shift type so the
 * operator can see what to hire for.
 */

import { isoWeekdayMon0 } from "@/lib/labor/staffing-need";
import type { ShiftKind } from "@/lib/labor/shift-draft";

export type OpenShift = {
  date: string;
  startMin: number;
  endMin: number;
  hours: number;
  /** "adp" = posted open in ADP; "draft" = the draft needs it but nobody available fits. */
  source: "adp" | "draft";
  /** Someone the draft suggests for an ADP open shift. */
  suggested?: string | null;
};

const hm = (h: number, m = 0) => h * 60 + m;

/** Opener starts by 7:30 AM, closer runs to 8 PM or later, else mid — same as the draft's labels. */
export function shiftKind(startMin: number, endMin: number): ShiftKind {
  return startMin <= hm(7, 30) ? "open" : endMin >= hm(20) ? "close" : "mid";
}

export type Cell = { shifts: number; hours: number };

export type OpenShiftSummary = {
  /** [weekday Mon = 0][open | mid | close] */
  grid: Record<ShiftKind, Cell>[];
  total: Cell;
  /** Shifts still needing someone (ADP open with no suggestion, plus unassigned draft). */
  unfilled: Cell;
  /** Most unfilled shifts on one date — people can work one shift a day, so at least this many new hires. */
  peak: { date: string; shifts: number } | null;
};

const empty = (): Cell => ({ shifts: 0, hours: 0 });

export function summarizeOpenShifts(rows: OpenShift[]): OpenShiftSummary {
  const grid = Array.from({ length: 7 }, () => ({ open: empty(), mid: empty(), close: empty() }));
  const total = empty();
  const unfilled = empty();
  const perDate = new Map<string, number>();
  for (const r of rows) {
    const c = grid[isoWeekdayMon0(r.date)]![shiftKind(r.startMin, r.endMin)];
    c.shifts += 1;
    c.hours += r.hours;
    total.shifts += 1;
    total.hours += r.hours;
    if (!r.suggested) {
      unfilled.shifts += 1;
      unfilled.hours += r.hours;
      perDate.set(r.date, (perDate.get(r.date) ?? 0) + 1);
    }
  }
  let peak: OpenShiftSummary["peak"] = null;
  for (const [date, shifts] of [...perDate].sort(([a], [b]) => a.localeCompare(b))) {
    if (!peak || shifts > peak.shifts) peak = { date, shifts };
  }
  return { grid, total, unfilled, peak };
}

/**
 * Rough hires to cover the unfilled shifts: enough people for the weekly hours
 * at `capHours` each, and at least one per shift on the busiest date.
 */
export function peopleToHire(s: OpenShiftSummary, weeks: number, capHours: number): number {
  if (!(s.unfilled.hours > 0) || !(weeks > 0) || !(capHours > 0)) return 0;
  return Math.max(Math.ceil(s.unfilled.hours / weeks / capHours), s.peak?.shifts ?? 0);
}
