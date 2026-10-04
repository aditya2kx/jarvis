/**
 * Draft open shifts for a day (Issue #337).
 * Greedy: repeatedly add the historical shift block (open / mid / close) that
 * covers the most short 15-min steps, give it to the best available person
 * (furthest below their target weekly hours first, then whoever already has the
 * most hours — fewer people overall), trimmed around their ADP unavailability.
 * No one fits → unassigned.
 */

import { isoWeekdayMon0 } from "@/lib/labor/staffing-need";
import { freeSegment, type Block } from "@/lib/labor/unavailability";

export type ShiftKind = "open" | "mid" | "close";
export type ShiftTemplate = { kind: ShiftKind; startMin: number; endMin: number };

/** Per-person availability; `windows[dow]` null = unavailable that weekday. */
export type Availability = {
  employee: string;
  maxWeekHours: number;
  windows: ([number, number] | null)[];
  /** Operator rule: aim for about this many hours a week (also the cap). */
  targetWeekHours?: number;
  /** Operator rule: at most this many shifts per pay period. */
  maxShiftsPerPeriod?: number;
  /** Operator rule: final working day (YYYY-MM-DD). */
  lastDay?: string;
};

const worksOn = (a: Availability, iso: string) => a.lastDay == null || iso <= a.lastDay;

export type DraftShift = {
  date: string;
  kind: ShiftKind;
  startMin: number;
  endMin: number;
  hours: number;
  employee: string | null;
  /** Shortened from the historical block to fit the person's availability. */
  trimmed: boolean;
  /** Suggested person for an existing ADP open shift — already counted as coverage and hours. */
  fillsOpen?: boolean;
};

const hm = (h: number, m = 0) => h * 60 + m;

/**
 * Median open / mid / close blocks per weekday (Mon = 0), ADP punches
 * 2026-07-27 → 2026-09-25, rounded to 15 min. The 6:30 opener and 7:30 second
 * opener follow the labor floor; open end, mid and close follow history.
 */
export const HISTORICAL_TEMPLATES: ShiftTemplate[][] = [
  [[14, 0], [10, 0, 17, 0], [13, 30, 20, 30]],
  [[14, 0], [10, 0, 16, 30], [13, 30, 20, 45]],
  [[14, 45], [9, 30, 16, 0], [13, 30, 20, 30]],
  [[14, 0], [9, 15, 16, 30], [13, 30, 20, 30]],
  [[14, 0], [9, 0, 13, 30], [13, 30, 20, 45]],
  [[13, 45], [10, 0, 18, 45], [13, 30, 20, 30]],
  [[14, 0], [10, 0, 17, 0], [13, 30, 21, 0]],
].map(([openEnd, mid, close]) => [
  { kind: "open", startMin: hm(6, 30), endMin: hm(openEnd![0]!, openEnd![1]) },
  { kind: "open", startMin: hm(7, 30), endMin: hm(openEnd![0]!, openEnd![1]) },
  { kind: "mid", startMin: hm(mid![0]!, mid![1]), endMin: hm(mid![2]!, mid![3]) },
  { kind: "close", startMin: hm(close![0]!, close![1]), endMin: hm(close![2]!, close![3]) },
]);

function gain(short: number[], mins: number[], s: number, e: number): number {
  let g = 0;
  mins.forEach((t, i) => {
    if (t >= s && t < e && short[i]! > 0) g += 1;
  });
  return g;
}

export function draftDay(args: {
  iso: string;
  /** Minute-of-day per 15-min step. */
  mins: number[];
  /** Headcount already on (clocked or scheduled) per step. */
  onFloor: number[];
  need: number[];
  roster: Availability[];
  /** Hours already committed this week, per employee (mutated). */
  weekHours: Map<string, number>;
  /** Employees already working this day. */
  busy: Set<string>;
  /** Shifts already in this day's pay period, per employee (mutated). */
  periodShifts?: Map<string, number>;
  /** ADP unavailability on this day, per employee. */
  unavailable?: Map<string, Block[]>;
  templates?: ShiftTemplate[];
  minShiftMin?: number;
  maxShifts?: number;
}): DraftShift[] {
  const dow = isoWeekdayMon0(args.iso);
  const templates = args.templates ?? HISTORICAL_TEMPLATES[dow]!;
  const minLen = args.minShiftMin ?? 240;
  const cover = [...args.onFloor];
  const busy = new Set(args.busy);
  const out: DraftShift[] = [];
  const weekOf = (a: Availability) => args.weekHours.get(a.employee) ?? 0;
  const deficit = (a: Availability) =>
    a.targetWeekHours == null ? 0 : Math.max(0, a.targetWeekHours - weekOf(a));

  for (let n = 0; n < (args.maxShifts ?? 8); n++) {
    const short = args.need.map((x, i) => x - cover[i]!);
    const firstIdx = short.findIndex((x) => x > 0);
    if (firstIdx < 0) break;
    const t0 = args.mins[firstIdx]!;
    // Interval-cover sweep: earliest short step, block that reaches furthest.
    const best =
      templates
        .filter((t) => t.startMin <= t0 && t0 < t.endMin)
        .sort((a, b) => b.endMin - a.endMin || b.startMin - a.startMin)[0] ??
      templates.reduce<ShiftTemplate | null>(
        (acc, t) =>
          gain(short, args.mins, t.startMin, t.endMin) >
          (acc ? gain(short, args.mins, acc.startMin, acc.endMin) : 0)
            ? t
            : acc,
        null,
      );
    if (!best) break;
    const mustCover = best.startMin <= t0 && t0 < best.endMin ? t0 : null;
    // Start when someone is first short (e.g. a day rule's 8:00), not at the
    // template's earlier start — but never shorter than the minimum shift.
    const blockStart =
      mustCover == null ? best.startMin : Math.max(best.startMin, Math.min(t0, best.endMin - minLen));

    const candidates = args.roster
      .filter((a) => !busy.has(a.employee) && a.windows[dow] && worksOn(a, args.iso))
      .map((a) => {
        const [ws, we] = a.windows[dow]!;
        const [s, e] = freeSegment(
          Math.max(ws, blockStart),
          Math.min(we, best!.endMin),
          args.unavailable?.get(a.employee),
        );
        return { a, s, e, len: e - s };
      })
      .filter(
        ({ a, s, e, len }) =>
          len >= minLen &&
          weekOf(a) + len / 60 <= (a.targetWeekHours ?? a.maxWeekHours) &&
          (a.maxShiftsPerPeriod == null ||
            (args.periodShifts?.get(a.employee) ?? 0) < a.maxShiftsPerPeriod) &&
          (mustCover == null ? gain(short, args.mins, s, e) > 0 : s <= mustCover && mustCover < e),
      )
      .sort(
        (x, y) =>
          deficit(y.a) - deficit(x.a) ||
          weekOf(y.a) - weekOf(x.a) ||
          x.a.employee.localeCompare(y.a.employee),
      );

    const pick = candidates[0];
    const s = pick ? pick.s : blockStart;
    const e = pick ? pick.e : best.endMin;
    out.push({
      date: args.iso,
      kind: best.kind,
      startMin: s,
      endMin: e,
      hours: (e - s) / 60,
      employee: pick?.a.employee ?? null,
      trimmed: !!pick && (s !== blockStart || e !== best.endMin),
    });
    if (pick) {
      busy.add(pick.a.employee);
      args.weekHours.set(pick.a.employee, (args.weekHours.get(pick.a.employee) ?? 0) + (e - s) / 60);
      args.periodShifts?.set(pick.a.employee, (args.periodShifts.get(pick.a.employee) ?? 0) + 1);
    }
    args.mins.forEach((t, i) => {
      if (t >= s && t < e) cover[i]! += 1;
    });
  }
  return out.sort((a, b) => a.startMin - b.startMin);
}

/**
 * Suggest who takes an ADP open shift: someone free for the whole slot, not
 * past their last day, within their weekly hours / shift caps, ranked like `draftDay`. The slot's
 * time is the operator's, so no trimming — nobody fits → null.
 */
export function fillOpenShift(args: {
  iso: string;
  startMin: number;
  endMin: number;
  roster: Availability[];
  /** Mutated on a pick. */
  weekHours: Map<string, number>;
  /** Mutated on a pick. */
  busy: Set<string>;
  /** Mutated on a pick. */
  periodShifts?: Map<string, number>;
  unavailable?: Map<string, Block[]>;
}): DraftShift | null {
  const { startMin: s, endMin: e } = args;
  const dow = isoWeekdayMon0(args.iso);
  const hours = (e - s) / 60;
  const weekOf = (a: Availability) => args.weekHours.get(a.employee) ?? 0;
  const deficit = (a: Availability) =>
    a.targetWeekHours == null ? 0 : Math.max(0, a.targetWeekHours - weekOf(a));
  const pick = args.roster
    .filter((a) => {
      const w = a.windows[dow];
      if (args.busy.has(a.employee) || !w || w[0] > s || w[1] < e || !worksOn(a, args.iso)) return false;
      const [fs, fe] = freeSegment(s, e, args.unavailable?.get(a.employee));
      return (
        fs === s &&
        fe === e &&
        weekOf(a) + hours <= (a.targetWeekHours ?? a.maxWeekHours) &&
        (a.maxShiftsPerPeriod == null ||
          (args.periodShifts?.get(a.employee) ?? 0) < a.maxShiftsPerPeriod)
      );
    })
    .sort(
      (x, y) => deficit(y) - deficit(x) || weekOf(y) - weekOf(x) || x.employee.localeCompare(y.employee),
    )[0];
  if (!pick) return null;
  args.busy.add(pick.employee);
  args.weekHours.set(pick.employee, weekOf(pick) + hours);
  args.periodShifts?.set(pick.employee, (args.periodShifts.get(pick.employee) ?? 0) + 1);
  const kind: ShiftKind = s <= hm(7, 30) ? "open" : e >= hm(20) ? "close" : "mid";
  return { date: args.iso, kind, startMin: s, endMin: e, hours, employee: pick.employee, trimmed: false, fillsOpen: true };
}

const STORE_HOURS: [number, number] = [hm(6), hm(21)];

/**
 * Everyone who worked or is scheduled in the window, available store hours
 * every day up to 40 h/week; ADP unavailability and staff rules narrow it.
 */
export function adpRoster(hoursByEmployee: Map<string, number>): Availability[] {
  return [...hoursByEmployee.keys()]
    .sort((a, b) => (hoursByEmployee.get(b) ?? 0) - (hoursByEmployee.get(a) ?? 0) || a.localeCompare(b))
    .map((employee) => ({ employee, maxWeekHours: 40, windows: Array(7).fill(STORE_HOURS) }));
}
