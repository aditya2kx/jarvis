/**
 * Draft open shifts for a day (Issue #337).
 * Greedy: repeatedly add a shift at the first short 15-min step — the historical
 * open / mid / close block, or (shift times "need") the short run itself, split
 * evenly past the longest shift — and give it to the best available person
 * (see `byPriority`: required shifts keep caps lasting all week for coverage,
 * spare shifts favour regulars), trimmed around their ADP unavailability. When
 * nobody can start the gap, someone free for part of it takes that part.
 * No one fits → unassigned.
 */

import { isoWeekdayMon0 } from "@/lib/labor/staffing-need";
import type { ShiftTimes } from "@/lib/labor/schedule-inputs";
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
  /** Operator rule: this person's longest shift (minutes) — longer or shorter than the store's. */
  maxShiftMin?: number;
  /** Operator rule: at least this many shifts a week before regulars take more. */
  minWeekShifts?: number;
  /** Hours worked + scheduled in the recent window — regulars (most) get spare shifts first. */
  recentHours?: number;
  /** Operator rule: at most this many shifts per pay period. */
  maxShiftsPerPeriod?: number;
  /** Operator rule: final working day (YYYY-MM-DD). */
  lastDay?: string;
};

const worksOn = (a: Availability, iso: string) => a.lastDay == null || iso <= a.lastDay;

export const weekCap = (a: Availability) => a.targetWeekHours ?? a.maxWeekHours;

/**
 * "coverage" (required shifts): hour targets by deficit, anyone under their weekly minimum shifts,
 * then most hours left under the cap — so the few people free on scarce days aren't capped out
 * early — then regulars (most recent hours). "regulars" (spare busy-time shifts) skips the
 * hours-left step so regulars take them up to their cap.
 */
export type DraftPreference = "coverage" | "regulars";

function byPriority(
  weekOf: (a: Availability) => number,
  shiftsOf: (a: Availability) => number,
  prefer: DraftPreference = "coverage",
) {
  const deficit = (a: Availability) =>
    a.targetWeekHours == null ? 0 : Math.max(0, a.targetWeekHours - weekOf(a));
  const belowMin = (a: Availability) => Math.max(0, (a.minWeekShifts ?? 0) - shiftsOf(a));
  const left = (a: Availability) => (prefer === "coverage" ? weekCap(a) - weekOf(a) : 0);
  return (x: Availability, y: Availability) =>
    deficit(y) - deficit(x) ||
    belowMin(y) - belowMin(x) ||
    left(y) - left(x) ||
    (y.recentHours ?? 0) - (x.recentHours ?? 0) ||
    x.employee.localeCompare(y.employee);
}

/** Could this person work a minimum shift on `iso` (weekday window, last day, ADP unavailability)? */
export function canWorkDay(a: Availability, iso: string, blocks: readonly Block[] | undefined, minShiftMin: number): boolean {
  const w = a.windows[isoWeekdayMon0(iso)];
  if (!w || !worksOn(a, iso)) return false;
  const [s, e] = freeSegment(w[0], w[1], blocks);
  return e - s >= minShiftMin;
}

/** How many people could work a minimum shift on `iso` — the draft staffs the scarcest days first. */
export function availableCount(args: {
  iso: string;
  roster: Availability[];
  unavailable?: Map<string, Block[]>;
  minShiftMin: number;
}): number {
  return args.roster.filter((a) => canWorkDay(a, args.iso, args.unavailable?.get(a.employee), args.minShiftMin)).length;
}

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

/** Drafted shifts start and end on the hour or half hour. */
const GRID = 30;
const snapDown = (t: number) => Math.floor(t / GRID) * GRID;
const snapUp = (t: number) => Math.ceil(t / GRID) * GRID;

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
  /** Shifts already this week, per employee (mutated) — for the weekly minimum. */
  weekShifts?: Map<string, number>;
  /** Employees already working this day. */
  busy: Set<string>;
  /** Shifts already in this day's pay period, per employee (mutated). */
  periodShifts?: Map<string, number>;
  /** ADP unavailability on this day, per employee. */
  unavailable?: Map<string, Block[]>;
  templates?: ShiftTemplate[];
  minShiftMin?: number;
  /** Longest shift ("need" shift times). */
  maxShiftMin?: number;
  /** Minutes an arriving person overlaps the one leaving (0 = back-to-back). */
  handoverOverlapMin?: number;
  shiftTimes?: ShiftTimes;
  maxShifts?: number;
  /** Days left this week (including this one) the person could still be drafted — paces hour targets. */
  daysLeft?: (employee: string) => number;
  prefer?: DraftPreference;
}): DraftShift[] {
  const dow = isoWeekdayMon0(args.iso);
  const templates = args.templates ?? HISTORICAL_TEMPLATES[dow]!;
  const minLen = args.minShiftMin ?? 240;
  const maxLen = Math.max(minLen, args.maxShiftMin ?? 8 * 60);
  const overlap = args.handoverOverlapMin ?? 0;
  const cover = [...args.onFloor];
  const busy = new Set(args.busy);
  const out: DraftShift[] = [];
  const weekOf = (a: Availability) => args.weekHours.get(a.employee) ?? 0;
  const priority = byPriority(weekOf, (a) => args.weekShifts?.get(a.employee) ?? 0, args.prefer);

  const step = args.mins.length > 1 ? args.mins[1]! - args.mins[0]! : 15;
  const needIdx = args.need.flatMap((x, i) => (x > 0 ? [i] : []));
  const dayStart = needIdx.length ? args.mins[needIdx[0]!]! : 0;
  const dayEnd = needIdx.length ? args.mins[needIdx[needIdx.length - 1]!]! + step : 24 * 60;

  /** "history": the template block covering the first short step, trimmed to the short span. */
  const templateBlock = (short: number[], t0: number) => {
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
    if (!best) return null;
    const mustCover = best.startMin <= t0 && t0 < best.endMin ? t0 : null;
    // Start when someone is first short (e.g. a day rule's 8:00), not at the
    // template's earlier start — but never shorter than the minimum shift.
    const start =
      mustCover == null ? best.startMin : Math.max(best.startMin, Math.min(t0, best.endMin - minLen));
    // Likewise end after the last short step (e.g. staffed until 8:30, not the template's 8:45).
    const lastShort = args.mins.reduce(
      (acc, t, i) => (t >= start && t < best.endMin && short[i]! > 0 ? t + step : acc),
      start,
    );
    return { kind: best.kind, start, end: Math.min(best.endMin, Math.max(lastShort, start + minLen)), mustCover };
  };

  /** "need": the unbroken short run from the first short step, split evenly past the longest shift. */
  const needBlock = (short: number[], firstIdx: number) => {
    let j = firstIdx;
    while (j + 1 < short.length && short[j + 1]! > 0) j++;
    const t0 = args.mins[firstIdx]!;
    const run = args.mins[j]! + step - t0;
    const parts = Math.ceil(run / maxLen);
    const len = Math.max(minLen, Math.ceil(run / parts / step) * step);
    const end = Math.min(dayEnd, t0 + len);
    const start = Math.max(dayStart, Math.min(t0, end - len));
    const kind: ShiftKind = start <= dayStart ? "open" : end >= dayEnd ? "close" : "mid";
    return { kind, start, end, mustCover: t0 };
  };

  const extendNeighbour = (g0: number, g1: number): boolean => {
    const fits = out
      .filter((o) => o.employee != null && (o.endMin === g0 || o.startMin === g1))
      .flatMap((o) => {
        const a = args.roster.find((r) => r.employee === o.employee);
        const w = a?.windows[dow];
        if (!a || !w) return [];
        const s = Math.max(dayStart, snapDown(Math.min(o.startMin, g0)));
        const e = Math.min(dayEnd, snapUp(Math.max(o.endMin, g1)));
        const [fs, fe] = freeSegment(s, e, args.unavailable?.get(a.employee));
        const added = (e - s - (o.endMin - o.startMin)) / 60;
        const ok =
          s >= w[0] && e <= w[1] && fs === s && fe === e &&
          e - s <= (a.maxShiftMin ?? maxLen) &&
          weekOf(a) + added <= weekCap(a);
        return ok ? [{ o, a, s, e, added }] : [];
      })
      .sort((x, y) => x.added - y.added || priority(x.a, y.a));
    const f = fits[0];
    if (!f) return false;
    args.weekHours.set(f.a.employee, weekOf(f.a) + f.added);
    args.mins.forEach((t, i) => {
      if ((t >= f.s && t < f.o.startMin) || (t >= f.o.endMin && t < f.e)) cover[i]! += 1;
    });
    Object.assign(f.o, { startMin: f.s, endMin: f.e, hours: (f.e - f.s) / 60 });
    return true;
  };

  for (let n = 0; n < (args.maxShifts ?? 8); n++) {
    const short = args.need.map((x, i) => x - cover[i]!);
    const firstIdx = short.findIndex((x) => x > 0);
    if (firstIdx < 0) break;
    let gapIdx = firstIdx;
    while (gapIdx + 1 < short.length && short[gapIdx + 1]! > 0) gapIdx++;
    const g0 = args.mins[firstIdx]!;
    const g1 = args.mins[gapIdx]! + step;
    // A gap shorter than the shortest shift: stretch a drafted neighbour over it rather than
    // add a minimum-length shift that overstaffs the rest of its span.
    if (g1 - g0 < minLen && extendNeighbour(g0, g1)) {
      n--;
      continue;
    }
    const block = args.shiftTimes === "need" ? needBlock(short, firstIdx) : templateBlock(short, args.mins[firstIdx]!);
    if (!block) break;
    let { start: blockStart, end: blockEnd } = block;
    const { mustCover } = block;
    // Nobody arrives the minute someone leaves: overlap the handover instead.
    if (overlap > 0) {
      const at = (t: number) => args.mins.indexOf(t);
      const i = at(blockStart);
      if (i > 0 && cover[i - 1]! > cover[i]!) blockStart = Math.max(dayStart, blockStart - overlap);
      const j = at(blockEnd);
      if (j > 0 && cover[j]! > cover[j - 1]!) blockEnd = Math.min(dayEnd, blockEnd + overlap);
    }
    blockStart = snapDown(blockStart);
    blockEnd = snapUp(blockEnd);

    // A person with a longer own longest shift may stay on through the rest of the short run.
    let runEnd = firstIdx;
    while (runEnd + 1 < short.length && short[runEnd + 1]! > 0) runEnd++;
    const reach = Math.min(dayEnd, Math.max(blockEnd, args.mins[runEnd]! + step));

    const eligible = args.roster
      .filter((a) => !busy.has(a.employee) && a.windows[dow] && worksOn(a, args.iso))
      .map((a) => {
        const [ws, we] = a.windows[dow]!;
        const lo = Math.max(ws, blockStart);
        let hi = Math.min(we, blockEnd);
        const own = a.maxShiftMin;
        if (own != null) {
          hi = own > blockEnd - blockStart ? Math.min(we, Math.min(reach, blockStart + own)) : Math.min(hi, lo + own);
        }
        const [s0, free0] = freeSegment(lo, hi, args.unavailable?.get(a.employee));
        const s = snapUp(s0);
        const free = snapDown(free0);
        let e = own != null ? Math.min(free, s + own) : free;
        // Spread an hour target evenly over as few days as their longest shift allows
        // (40 h at 9 h → 5 days of 8 h; at 7 h → 6 days of 6.75 h), never more days than are left.
        const left = a.targetWeekHours != null && args.daysLeft ? args.daysLeft(a.employee) : 0;
        if (left > 0) {
          const remaining = (a.targetWeekHours! - weekOf(a)) * 60;
          const days = Math.max(1, Math.min(left, Math.ceil(remaining / (own ?? maxLen))));
          e = Math.min(e, s + Math.max(minLen, Math.ceil(remaining / days / step) * step));
        }
        // A last shift may end early to land on the weekly cap instead of being skipped.
        e = snapDown(Math.min(e, s + Math.floor(((weekCap(a) - weekOf(a)) * 60) / step) * step));
        // "Trimmed" = cut short by their availability, not by pacing or their own longest shift.
        const trimmed = s > lo || free < hi || lo > blockStart || (own == null && hi < blockEnd);
        return { a, s, e, len: e - s, gain: gain(short, args.mins, s, e), trimmed };
      })
      .filter(
        ({ a, len, gain }) =>
          len >= minLen &&
          gain > 0 &&
          weekOf(a) + len / 60 <= weekCap(a) &&
          (a.maxShiftsPerPeriod == null ||
            (args.periodShifts?.get(a.employee) ?? 0) < a.maxShiftsPerPeriod),
      );
    const starts = eligible
      .filter(({ s, e }) => mustCover == null || (s <= mustCover && mustCover < e))
      .sort((x, y) => priority(x.a, y.a));
    // Nobody can start the gap: whoever covers the most of it takes their part.
    const pick =
      starts[0] ??
      eligible.sort((x, y) => y.gain - x.gain || priority(x.a, y.a))[0];
    const s = pick ? pick.s : blockStart;
    const e = pick ? pick.e : blockEnd;
    out.push({
      date: args.iso,
      kind: block.kind,
      startMin: s,
      endMin: e,
      hours: (e - s) / 60,
      employee: pick?.a.employee ?? null,
      trimmed: !!pick && pick.trimmed,
    });
    if (pick) {
      busy.add(pick.a.employee);
      args.weekHours.set(pick.a.employee, (args.weekHours.get(pick.a.employee) ?? 0) + (e - s) / 60);
      args.weekShifts?.set(pick.a.employee, (args.weekShifts.get(pick.a.employee) ?? 0) + 1);
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
  weekShifts?: Map<string, number>;
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
  const pick = args.roster
    .filter((a) => {
      const w = a.windows[dow];
      if (args.busy.has(a.employee) || !w || w[0] > s || w[1] < e || !worksOn(a, args.iso)) return false;
      const [fs, fe] = freeSegment(s, e, args.unavailable?.get(a.employee));
      return (
        fs === s &&
        fe === e &&
        (a.maxShiftMin == null || e - s <= a.maxShiftMin) &&
        weekOf(a) + hours <= weekCap(a) &&
        (a.maxShiftsPerPeriod == null ||
          (args.periodShifts?.get(a.employee) ?? 0) < a.maxShiftsPerPeriod)
      );
    })
    .sort(byPriority(weekOf, (a) => args.weekShifts?.get(a.employee) ?? 0))[0];
  if (!pick) return null;
  args.busy.add(pick.employee);
  args.weekHours.set(pick.employee, weekOf(pick) + hours);
  args.weekShifts?.set(pick.employee, (args.weekShifts.get(pick.employee) ?? 0) + 1);
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
    .map((employee) => ({
      employee,
      maxWeekHours: 40,
      windows: Array(7).fill(STORE_HOURS),
      recentHours: hoursByEmployee.get(employee) ?? 0,
    }));
}
