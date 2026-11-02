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

import {
  CLOSER_FROM,
  isTrainingShift,
  OPENER_BY,
  overlapMinutes,
  shiftKindOf,
  type TrainingLeft,
} from "@/lib/labor/new-hire-training";
import { isoWeekdayMon0 } from "@/lib/labor/staffing-need";
import type { ShiftTimes, ShiftWindowRule } from "@/lib/labor/schedule-inputs";
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
  /** New hires train beside this person (Issue #450). */
  experienced?: boolean;
  /** Experienced, but trains only openers or only closers. */
  trainsOnly?: "open" | "close";
  /** Operator rule: on these weekdays each shift fits entirely inside one window. */
  shiftWindows?: ShiftWindowRule[];
};

/**
 * Spans a shift for this person must fit inside on weekday `dow`: their day window, or each of
 * their shift windows (the last rule covering `dow`) clipped to it. Empty = can't work that weekday.
 */
export function shiftSpans(a: Availability, dow: number): [number, number][] {
  const w = a.windows[dow];
  if (!w) return [];
  const rule = a.shiftWindows?.findLast((r) => r.days.includes(dow));
  if (!rule) return [w];
  return rule.windows
    .map(([s, e]): [number, number] => [Math.max(s, w[0]), Math.min(e, w[1])])
    .filter(([s, e]) => e > s);
}

const insideSpan = (spans: [number, number][], s: number, e: number) => spans.some(([ws, we]) => ws <= s && e <= we);

/**
 * New-hire training for one day (Issue #450): someone in `left` works an opener / closer like
 * anyone else once they've done that kind's supervised shifts. Until then a shift of that kind —
 * and any mid — is a training shift: drafted on top of need beside experienced staff overlapping
 * it by `overlapMin`, never toward need.
 */
export type DraftTraining = {
  overlapMin: number;
  /** New hires who may shadow one experienced shift at once (default 1). */
  perTrainer?: number;
  /** Supervised openers / closers still to do, per new hire (mutated as the draft gives them some). */
  left: Map<string, TrainingLeft>;
  /** Experienced people already on this day (ADP or an earlier draft pass). */
  experiencedOn: [number, number][];
};

export const TRAINING_ON_TOP = "New hire — trains on top of need, beside experienced staff";

/** Why a new hire can't take a `kind` shift toward need, or null when they can. */
export function trainingBlock(training: DraftTraining | undefined, employee: string, kind: ShiftKind): string | null {
  return isTrainingShift(training?.left.get(employee), kind) ? TRAINING_ON_TOP : null;
}

/** A supervised shift counts toward the new hire's openers / closers. */
function countTraining(training: DraftTraining, employee: string, kind: ShiftKind) {
  const left = training.left.get(employee);
  if (left && kind !== "mid" && left[kind] > 0) training.left.set(employee, { ...left, [kind]: left[kind] - 1 });
}

const worksOn = (a: Availability, iso: string) => a.lastDay == null || iso <= a.lastDay;

export const weekCap = (a: Availability) => a.targetWeekHours ?? a.maxWeekHours;

/** ADP meal policy: shifts longer than `afterMin` lose `unpaidMin` of paid time. */
export type MealRule = { afterMin: number; unpaidMin: number };

/** Paid hours for a shift, as ADP counts them (unpaid meal removed). */
export function paidHours(startMin: number, endMin: number, meal?: MealRule): number {
  const len = endMin - startMin;
  return (meal && len > meal.afterMin ? len - meal.unpaidMin : len) / 60;
}

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
  if (!worksOn(a, iso)) return false;
  return shiftSpans(a, isoWeekdayMon0(iso)).some(([ws, we]) => {
    const [s, e] = freeSegment(ws, we, blocks);
    return e - s >= minShiftMin;
  });
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
  /** A saved shift ADP's synced schedule already shows — counted there, not as suggested. */
  inAdp?: boolean;
  /** Saved for a person, but ADP now shows it as an open shift (e.g. approved unavailability). */
  adpOpen?: boolean;
  /** Saved as an open shift that this person has since taken in ADP. */
  takenBy?: string;
  /** Stable id of the draft step behind this shift — the key for an operator pick. */
  slot?: string;
  /** Everyone who could take this shift, best fit first; `employee: null` = leave it open. */
  options?: ShiftOption[];
  /** The operator picked this person (or left it open) instead of the suggestion. */
  picked?: boolean;
  /** New hires who could otherwise take it, and why they can't yet (Issue #450). */
  blocked?: { employee: string; reason: string }[];
  /** A new hire's training shift beside experienced staff — on top of need, not counted toward it. */
  training?: boolean;
};

/** One person who could take a drafted shift, on their own times (trimmed / paced). */
export type ShiftOption = {
  employee: string | null;
  startMin: number;
  endMin: number;
  hours: number;
  trimmed: boolean;
  /** Paid hours already this week, before this shift. */
  weekHours?: number;
  /** Their weekly cap (hour target, else max hours). */
  cap?: number;
  target?: number;
};

/** Operator picks by shift slot: a person, or null to leave the shift open. */
export type DraftPins = ReadonlyMap<string, string | null>;

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

/** One step's alternative: give it to `person` (null = leave open) and/or as one longest, unpaced shift. */
type DraftChoice = { person?: string | null; long?: boolean };

type DraftDayArgs = {
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
  /** Shift and weekly hours are paid hours under this rule (none = clock time). */
  meal?: MealRule;
  shiftTimes?: ShiftTimes;
  maxShifts?: number;
  /** Days left this week (including this one) the person could still be drafted — paces hour targets. */
  daysLeft?: (employee: string) => number;
  prefer?: DraftPreference;
  /** Operator picks, keyed by `DraftShift.slot`; one whose person no longer fits is ignored. */
  pins?: DraftPins;
  /** Slot ids start with this (default `iso`) — keeps separate draft passes on a day apart. */
  slotPrefix?: string;
  training?: DraftTraining;
};

/**
 * Each step tries every alternative (anyone free, their longest shift instead of an even split,
 * or leaving it open), plays the rest of the day out greedily, and keeps the plan with the fewest
 * unstaffed (short or open) then total hours — so scarce days give people full shifts instead of
 * open ones. Ties keep the plain greedy plan, so hour-target pacing holds when it costs nothing.
 */
export function draftDay(args: DraftDayArgs): DraftShift[] {
  return draftCore(args, { lookahead: true }) ?? [];
}

function draftCore(
  args: DraftDayArgs,
  opts: {
    lookahead: boolean;
    force?: DraftChoice;
    seed?: DraftShift[];
    rolloutLong?: boolean;
  },
): DraftShift[] | null {
  const dow = isoWeekdayMon0(args.iso);
  const templates = args.templates ?? HISTORICAL_TEMPLATES[dow]!;
  const minLen = args.minShiftMin ?? 240;
  const maxLen = Math.max(minLen, args.maxShiftMin ?? 8 * 60);
  const overlap = args.handoverOverlapMin ?? 0;
  const cover = [...args.onFloor];
  const busy = new Set(args.busy);
  const out: DraftShift[] = (opts.seed ?? []).map((o) => ({ ...o }));
  const weekOf = (a: Availability) => args.weekHours.get(a.employee) ?? 0;
  const priority = byPriority(weekOf, (a) => args.weekShifts?.get(a.employee) ?? 0, args.prefer);
  const paid = (s: number, e: number) => paidHours(s, e, args.meal);
  /** A new hire free today still needs a `kind` shift and could start / finish one. */
  const wantsTrainer = (kind: "open" | "close") =>
    args.roster.some((a) => {
      if (!(args.training?.left.get(a.employee)?.[kind] ?? 0) || busy.has(a.employee) || !worksOn(a, args.iso)) return false;
      return shiftSpans(a, dow).some(([ws, we]) => {
        const [s, e] = freeSegment(ws, we, args.unavailable?.get(a.employee));
        return e - s >= minLen && (kind === "open" ? s <= OPENER_BY : e >= CLOSER_FROM);
      });
    });

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
  const needBlock = (short: number[], firstIdx: number, long: boolean) => {
    let j = firstIdx;
    while (j + 1 < short.length && short[j + 1]! > 0) j++;
    const t0 = args.mins[firstIdx]!;
    const run = args.mins[j]! + step - t0;
    const parts = long ? 1 : Math.ceil(run / maxLen);
    const len = Math.max(minLen, Math.min(maxLen, Math.ceil(run / parts / step) * step));
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
        if (!a) return [];
        const s = Math.max(dayStart, snapDown(Math.min(o.startMin, g0)));
        const e = Math.min(dayEnd, snapUp(Math.max(o.endMin, g1)));
        const [fs, fe] = freeSegment(s, e, args.unavailable?.get(a.employee));
        const added = paid(s, e) - paid(o.startMin, o.endMin);
        const ok =
          insideSpan(shiftSpans(a, dow), s, e) && fs === s && fe === e &&
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
    Object.assign(f.o, { startMin: f.s, endMin: f.e, hours: paid(f.s, f.e) });
    return true;
  };

  const maxShifts = args.maxShifts ?? 8;
  const bestChoice = (n: number): DraftChoice => {
    const free = args.roster
      .filter((a) => !busy.has(a.employee) && shiftSpans(a, dow).length && worksOn(a, args.iso))
      .map((a) => a.employee);
    const choices: DraftChoice[] = [
      {},
      { long: true },
      { person: null },
      ...free.flatMap((person) => [{ person }, { person, long: true }]),
    ];
    let best: DraftChoice = {};
    let bestScore: number[] | null = null;
    for (const force of choices) for (const rolloutLong of [false, true]) {
      const plan = draftCore(
        {
          ...args,
          onFloor: cover,
          busy,
          weekHours: new Map(args.weekHours),
          weekShifts: args.weekShifts && new Map(args.weekShifts),
          periodShifts: args.periodShifts && new Map(args.periodShifts),
          maxShifts: maxShifts - n,
        },
        { lookahead: false, force, seed: out, rolloutLong },
      );
      if (!plan) continue;
      const shortH =
        args.need.reduce(
          (acc, x, i) =>
            acc + Math.max(0, x - args.onFloor[i]! - plan.filter((p) => args.mins[i]! >= p.startMin && args.mins[i]! < p.endMin).length),
          0,
        ) * step / 60;
      const openH = plan.reduce((acc, p) => acc + (p.employee == null ? p.hours : 0), 0);
      const totalH = plan.reduce((acc, p) => acc + p.hours, 0);
      // Then keep new hires still training free for the training pass.
      const learners = plan.filter((p) => {
        const l = p.employee ? args.training?.left.get(p.employee) : undefined;
        return !!l && (l.open > 0 || l.close > 0);
      }).length;
      const score = [shortH + openH, learners, totalH];
      const k = bestScore ? score.findIndex((v, i) => Math.abs(v - bestScore![i]!) > 1e-9) : -1;
      if (!bestScore || (k >= 0 && score[k]! < bestScore[k]!)) {
        best = force;
        bestScore = score;
      }
    }
    return best;
  };

  for (let n = 0; n < maxShifts; n++) {
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
    // Seeded lookahead rollouts count the same shifts, so they derive the same slot ids.
    const slotBase = `${args.slotPrefix ?? args.iso}|${g0}|`;
    const slot = slotBase + out.filter((o) => o.slot?.startsWith(slotBase)).length;

    const candidates = (choice: DraftChoice) => {
      const block =
        args.shiftTimes === "need" ? needBlock(short, firstIdx, !!choice.long) : templateBlock(short, args.mins[firstIdx]!);
      if (!block) return null;
      let { start: blockStart, end: blockEnd } = block;
      const { mustCover } = block;
      // Nobody arrives the minute someone leaves: overlap the handover instead, but at most one
      // over the need — when two leave together, only one arrival overlaps.
      if (overlap > 0) {
        const at = (t: number) => args.mins.indexOf(t);
        const roomFor = (from: number, to: number) =>
          args.mins.every((t, k) => t < from || t >= to || cover[k]! <= args.need[k]!);
        const i = at(blockStart);
        const padStart = Math.max(dayStart, blockStart - overlap);
        if (i > 0 && cover[i - 1]! > cover[i]! && roomFor(padStart, blockStart)) blockStart = padStart;
        const j = at(blockEnd);
        const padEnd = Math.min(dayEnd, blockEnd + overlap);
        if (j > 0 && cover[j]! > cover[j - 1]! && roomFor(blockEnd, padEnd)) blockEnd = padEnd;
      }
      blockStart = snapDown(blockStart);
      blockEnd = snapUp(blockEnd);

      // A person with a longer own longest shift may stay on through the rest of the short run.
      let runEnd = firstIdx;
      while (runEnd + 1 < short.length && short[runEnd + 1]! > 0) runEnd++;
      const reach = Math.min(dayEnd, Math.max(blockEnd, args.mins[runEnd]! + step));

      const fitIn = (a: Availability, ws: number, we: number) => {
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
          if (left > 0 && !choice.long) {
            const remaining = (a.targetWeekHours! - weekOf(a)) * 60;
            const days = Math.max(1, Math.min(left, Math.ceil(remaining / (own ?? maxLen))));
            e = Math.min(e, s + Math.max(minLen, Math.ceil(remaining / days / step) * step));
          }
          // A last shift may end early to land on the weekly cap instead of being skipped.
          e = snapDown(Math.min(e, s + Math.floor(((weekCap(a) - weekOf(a)) * 60) / step) * step));
          // "Trimmed" = cut short by their availability, not by pacing or their own longest shift.
          const trimmed = s > lo || free < hi || lo > blockStart || (own == null && hi < blockEnd);
          return { a, s, e, len: e - s, gain: gain(short, args.mins, s, e), trimmed };
      };
      const covers = (x: { s: number; e: number }) => mustCover == null || (x.s <= mustCover && mustCover < x.e);
      const fits = args.roster
        .filter(
          (a) =>
            !busy.has(a.employee) &&
            worksOn(a, args.iso) &&
            (a.maxShiftsPerPeriod == null || (args.periodShifts?.get(a.employee) ?? 0) < a.maxShiftsPerPeriod),
        )
        .flatMap((a) => {
          // Shift windows: the span that starts the gap, else covers the most of it.
          const best = shiftSpans(a, dow)
            .map(([ws, we]) => fitIn(a, ws, we))
            .filter((x) => x.len >= minLen && x.gain > 0 && weekOf(a) + paid(0, x.len) <= weekCap(a))
            .sort((x, y) => Number(covers(y)) - Number(covers(x)) || y.gain - x.gain || y.len - x.len)[0];
          return best ? [best] : [];
        });
      const blocked = fits.flatMap(({ a }) => {
        const reason = trainingBlock(args.training, a.employee, block.kind);
        return reason ? [{ employee: a.employee, reason }] : [];
      });
      const eligible = fits.filter((x) => !blocked.some((b) => b.employee === x.a.employee));
      // Train sooner: experienced staff take the openers / closers a new hire can shadow today, and
      // leave every other shift to others while one could.
      const wantsOpen = wantsTrainer("open");
      const wantsClose = wantsTrainer("close");
      const trains = (a: Availability, kind: "open" | "close") =>
        !!a.experienced && (!a.trainsOnly || a.trainsOnly === kind);
      const wanted = (a: Availability, kind: "open" | "close") =>
        trains(a, kind) && (kind === "open" ? wantsOpen : wantsClose);
      const trainer = (x: (typeof eligible)[number]) =>
        block.kind !== "mid" && wanted(x.a, block.kind) ? -1 : wanted(x.a, "open") || wanted(x.a, "close") ? 1 : 0;
      // A new hire with openers / closers still to do takes a cleared kind only when nobody else
      // can, staying free to train beside a trainer that day.
      const learner = (x: (typeof eligible)[number]) => {
        const l = args.training?.left.get(x.a.employee);
        return l && (l.open > 0 || l.close > 0) ? 1 : 0;
      };
      const rank = (x: (typeof eligible)[number], y: (typeof eligible)[number]) =>
        trainer(x) - trainer(y) || learner(x) - learner(y);
      const starts = eligible.filter(covers).sort((x, y) => rank(x, y) || priority(x.a, y.a));
      // Nobody can start the gap: whoever covers the most of it takes their part.
      const rest = eligible
        .filter((x) => !starts.includes(x))
        .sort((x, y) => rank(x, y) || y.gain - x.gain || priority(x.a, y.a));
      return { kind: block.kind, blockStart, blockEnd, ranked: [...starts, ...rest], blocked };
    };

    const pin = args.pins?.get(slot) ?? null;
    let c = args.pins?.has(slot) ? candidates({ person: pin }) : null;
    if (c && pin !== null && !c.ranked.some((x) => x.a.employee === pin)) c = null;
    const pinned = c != null;
    let choice: DraftChoice = { person: pin };
    if (!c) {
      choice = (n === 0 ? opts.force : undefined) ?? (opts.lookahead ? bestChoice(n) : { long: opts.rolloutLong });
      c = candidates(choice);
    }
    if (!c) break;
    const { blockStart, blockEnd, ranked } = c;
    const pick =
      choice.person === null
        ? undefined
        : choice.person !== undefined
          ? ranked.find((x) => x.a.employee === choice.person)
          : ranked[0];
    if (choice.person && !pick) return null;
    const s = pick ? pick.s : blockStart;
    const e = pick ? pick.e : blockEnd;
    out.push({
      date: args.iso,
      kind: c.kind,
      startMin: s,
      endMin: e,
      hours: paid(s, e),
      employee: pick?.a.employee ?? null,
      trimmed: !!pick && pick.trimmed,
      slot,
      ...(opts.lookahead
        ? {
            options: [
              ...(pick ? [pick, ...ranked.filter((x) => x !== pick)] : ranked).map((x) => ({
                employee: x.a.employee,
                startMin: x.s,
                endMin: x.e,
                hours: paid(x.s, x.e),
                trimmed: x.trimmed,
                weekHours: weekOf(x.a),
                cap: weekCap(x.a),
                target: x.a.targetWeekHours,
              })),
              { employee: null, startMin: blockStart, endMin: blockEnd, hours: paid(blockStart, blockEnd), trimmed: false },
            ],
            ...(c.blocked.length ? { blocked: c.blocked } : {}),
          }
        : {}),
      ...(pinned ? { picked: true } : {}),
    });
    if (pick) {
      busy.add(pick.a.employee);
      args.weekHours.set(pick.a.employee, (args.weekHours.get(pick.a.employee) ?? 0) + paid(s, e));
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
/** Stable id of one ADP open shift: `iso|adp|start|end`, plus `|n` for the nth identical one that day. */
export function openShiftSlot(iso: string, startMin: number, endMin: number, nth = 0): string {
  return `${iso}|adp|${startMin}|${endMin}${nth ? `|${nth}` : ""}`;
}

export const isOpenShiftSlot = (slot: string) => slot.includes("|adp|");

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
  meal?: MealRule;
  /** Operator picks by slot (`openShiftSlot`); null isn't offered — the slot is already open. */
  pins?: DraftPins;
  /** Earlier open shifts with the same times that day. */
  nth?: number;
  training?: DraftTraining;
}): DraftShift | null {
  const { startMin: s, endMin: e } = args;
  const dow = isoWeekdayMon0(args.iso);
  const kind = shiftKindOf(s, e);
  const hours = paidHours(s, e, args.meal);
  const weekOf = (a: Availability) => args.weekHours.get(a.employee) ?? 0;
  const slot = openShiftSlot(args.iso, s, e, args.nth);
  const fits = args.roster
    .filter((a) => {
      if (args.busy.has(a.employee) || !insideSpan(shiftSpans(a, dow), s, e) || !worksOn(a, args.iso)) return false;
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
    .sort(byPriority(weekOf, (a) => args.weekShifts?.get(a.employee) ?? 0));
  const blocked = fits.flatMap((a) => {
    const reason = trainingBlock(args.training, a.employee, kind);
    return reason ? [{ employee: a.employee, reason }] : [];
  });
  const ranked = fits.filter((a) => !blocked.some((b) => b.employee === a.employee));
  const pin = args.pins?.get(slot);
  const pinned = ranked.find((a) => a.employee === pin);
  const pick = pinned ?? ranked[0];
  if (!pick) {
    // Only new hires fit: nobody to suggest, but the console says why (they still read as unfilled).
    return blocked.length
      ? { date: args.iso, kind, startMin: s, endMin: e, hours, employee: null, trimmed: false, fillsOpen: true, slot, blocked }
      : null;
  }
  const options: ShiftOption[] = [pick, ...ranked.filter((a) => a !== pick)].map((a) => ({
    employee: a.employee,
    startMin: s,
    endMin: e,
    hours,
    trimmed: false,
    weekHours: weekOf(a),
    cap: weekCap(a),
    target: a.targetWeekHours,
  }));
  args.busy.add(pick.employee);
  args.weekHours.set(pick.employee, weekOf(pick) + hours);
  args.weekShifts?.set(pick.employee, (args.weekShifts.get(pick.employee) ?? 0) + 1);
  args.periodShifts?.set(pick.employee, (args.periodShifts.get(pick.employee) ?? 0) + 1);
  return {
    date: args.iso,
    kind,
    startMin: s,
    endMin: e,
    hours,
    employee: pick.employee,
    trimmed: false,
    fillsOpen: true,
    slot,
    options,
    ...(pinned ? { picked: true } : {}),
    ...(blocked.length ? { blocked } : {}),
  };
}

/**
 * After the training pass: a shift left open only because new hires were still training goes to
 * whoever can take it now that `training.left` (as of this day) counts the training drafted on
 * earlier days — a new hire works a kind toward need from the day after its last training shift.
 * An operator's "leave open" pick stands.
 */
export function fillClearedTrainees(args: {
  iso: string;
  shifts: DraftShift[];
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
  meal?: MealRule;
  pins?: DraftPins;
  training: DraftTraining;
}): DraftShift[] {
  return args.shifts.map((s) => {
    if (s.employee || s.training || !s.blocked?.length || !s.slot || args.pins?.get(s.slot) === null) return s;
    if (s.blocked.every((b) => trainingBlock(args.training, b.employee, s.kind))) return s;
    const pin = args.pins?.get(s.slot);
    const fill = fillOpenShift({
      ...args,
      startMin: s.startMin,
      endMin: s.endMin,
      pins: pin ? new Map([[openShiftSlot(args.iso, s.startMin, s.endMin), pin]]) : undefined,
    });
    if (!fill?.employee) return s;
    const open: ShiftOption = { employee: null, startMin: s.startMin, endMin: s.endMin, hours: s.hours, trimmed: false };
    return {
      ...fill,
      slot: s.slot,
      fillsOpen: s.fillsOpen,
      options: s.fillsOpen ? fill.options : [...(fill.options ?? []), open],
    };
  });
}

/**
 * New hires' training shifts for one day (Issue #450), on top of need: each new hire with openers /
 * closers left shadows an experienced opener / closer already on that day (ADP or drafted), up to
 * `perTrainer` new hires per experienced shift, within their own availability, longest shift and weekly cap. Called
 * day by day from the earliest, so new hires clear as soon as trainers are on.
 */
export function draftTrainingDay(args: {
  iso: string;
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
  minShiftMin?: number;
  maxShiftMin?: number;
  meal?: MealRule;
  /** `left` is mutated as new hires get openers / closers. */
  training: DraftTraining;
}): DraftShift[] {
  const { training } = args;
  const dow = isoWeekdayMon0(args.iso);
  const minLen = args.minShiftMin ?? 240;
  const trainers = [...training.experiencedOn].sort((x, y) => x[0] - y[0]);
  const used = new Map<number, number>();
  const full = (i: number) => (used.get(i) ?? 0) >= (training.perTrainer ?? 1);
  const out: DraftShift[] = [];
  const trainees = args.roster
    .filter((a) => {
      const l = training.left.get(a.employee);
      return (
        !!l && (l.open > 0 || l.close > 0) &&
        !args.busy.has(a.employee) && worksOn(a, args.iso) &&
        (a.maxShiftsPerPeriod == null || (args.periodShifts?.get(a.employee) ?? 0) < a.maxShiftsPerPeriod)
      );
    })
    // Whoever can train on only one kind goes first, so someone flexible doesn't take their trainer.
    .sort((x, y) => {
      const lx = training.left.get(x.employee)!;
      const ly = training.left.get(y.employee)!;
      const kinds = (l: TrainingLeft) => Number(l.open > 0) + Number(l.close > 0);
      return (
        kinds(lx) - kinds(ly) ||
        ly.open + ly.close - (lx.open + lx.close) ||
        x.employee.localeCompare(y.employee)
      );
    });
  for (const a of trainees) {
    const left = training.left.get(a.employee)!;
    const kinds = (["open", "close"] as const).filter((k) => left[k] > 0).sort((x, y) => left[y] - left[x]);
    const len = Math.min(a.maxShiftMin ?? args.maxShiftMin ?? 8 * 60, args.maxShiftMin ?? Infinity);
    const room = snapDown((weekCap(a) - (args.weekHours.get(a.employee) ?? 0)) * 60);
    const fit = kinds.flatMap((kind) =>
      trainers.flatMap(([ts, te], i) => {
        if (full(i) || shiftKindOf(ts, te) !== kind) return [];
        return shiftSpans(a, dow).flatMap(([ws, we]) => {
          const [fs, fe] = freeSegment(Math.max(ts, ws), Math.min(te, we), args.unavailable?.get(a.employee));
          let s = snapUp(fs);
          let e = snapDown(fe);
          // Openers start with the trainer, closers finish with them.
          if (kind === "open") e = Math.min(e, s + len, s + room);
          else s = Math.max(s, e - len, e - room);
          const hours = paidHours(s, e, args.meal);
          const ok =
            e - s >= minLen &&
            shiftKindOf(s, e) === kind &&
            overlapMinutes(s, e, [[ts, te]]) >= training.overlapMin &&
            (args.weekHours.get(a.employee) ?? 0) + hours <= weekCap(a);
          return ok ? [{ kind, i, s, e, hours }] : [];
        });
      }),
    )[0];
    if (!fit) continue;
    used.set(fit.i, (used.get(fit.i) ?? 0) + 1);
    args.busy.add(a.employee);
    args.weekHours.set(a.employee, (args.weekHours.get(a.employee) ?? 0) + fit.hours);
    args.weekShifts?.set(a.employee, (args.weekShifts.get(a.employee) ?? 0) + 1);
    args.periodShifts?.set(a.employee, (args.periodShifts.get(a.employee) ?? 0) + 1);
    countTraining(training, a.employee, fit.kind);
    out.push({
      date: args.iso,
      kind: fit.kind,
      startMin: fit.s,
      endMin: fit.e,
      hours: fit.hours,
      employee: a.employee,
      trimmed: false,
      slot: `${args.iso}|t|${a.employee}`,
      training: true,
    });
  }
  return out;
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
