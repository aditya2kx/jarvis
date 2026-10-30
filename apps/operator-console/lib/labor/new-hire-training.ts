/**
 * New-hire training (Issue #450): someone hired within the last `newHireDays` works only beside an
 * experienced person until they have done `openShifts` openers and `closeShifts` closers that each
 * overlap an experienced person by `overlapMin`. A one-time requirement, counted over every ADP shift
 * since they started — clocked and scheduled.
 */

import { isOpenLane, type CoveragePersonDay } from "@/lib/labor/coverage-model";
import type { TrainingRules } from "@/lib/labor/schedule-inputs";
import type { ShiftKind } from "@/lib/labor/shift-draft";
import { shiftCalendarDate } from "@/lib/filters/range";

/** ADP open / close by clock time: starts by 7:30 AM = opener, ends 8 PM or later = closer. */
const OPENER_BY = 7 * 60 + 30;
const CLOSER_FROM = 20 * 60;

export function shiftKindOf(startMin: number, endMin: number): ShiftKind {
  return startMin <= OPENER_BY ? "open" : endMin >= CLOSER_FROM ? "close" : "mid";
}

/** Minutes of [s, e) that at least one span covers. */
export function overlapMinutes(s: number, e: number, spans: readonly (readonly [number, number])[]): number {
  const cut = spans
    .map(([a, b]) => [Math.max(a, s), Math.min(b, e)] as const)
    .filter(([a, b]) => b > a)
    .sort((x, y) => x[0] - y[0]);
  let total = 0;
  let end = s;
  for (const [a, b] of cut) {
    if (b <= end) continue;
    total += b - Math.max(a, end);
    end = b;
  }
  return total;
}

/** Supervised shifts still to do; absent = not (or no longer) a new hire. */
export type TrainingLeft = { open: number; close: number };

export type TrainingStatus = {
  employee: string;
  /** ADP hire date, when the scrape has it. */
  hireDate: string | null;
  /** The date the new-hire window counts from: hire date, else first ADP shift. */
  since: string;
  open: number;
  close: number;
  needOpen: number;
  needClose: number;
  cleared: boolean;
  /** Upcoming ADP shifts before they're cleared with no experienced person on long enough. */
  unsupervised: string[];
};

export const isTraining = (t: TrainingStatus | undefined): t is TrainingStatus => !!t && !t.cleared;

export function trainingLeftOf(t: TrainingStatus): TrainingLeft {
  return { open: Math.max(0, t.needOpen - t.open), close: Math.max(0, t.needClose - t.close) };
}

/** "1/2 openers · 0/2 closers". */
export function progressLabel(t: Pick<TrainingStatus, "open" | "close" | "needOpen" | "needClose">): string {
  const parts: string[] = [];
  if (t.needOpen) parts.push(`${Math.min(t.open, t.needOpen)}/${t.needOpen} openers`);
  if (t.needClose) parts.push(`${Math.min(t.close, t.needClose)}/${t.needClose} closers`);
  return parts.join(" · ");
}

function spansOf(p: CoveragePersonDay): [number, number][] {
  return p.segments.map((s) => [s.startMin, s.endMin]);
}

/**
 * Who is a new hire and how far along their supervised openers / closers are. `days` holds every
 * day's ADP lanes (clocked before today, scheduled from today) in the window; `employees` is the
 * draftable roster. Experienced staff are never new hires.
 */
export function trainingProgress(args: {
  todayIso: string;
  rules: TrainingRules;
  employees: readonly string[];
  hireDates: ReadonlyMap<string, string>;
  days: ReadonlyMap<string, CoveragePersonDay[]>;
}): Map<string, TrainingStatus> {
  const { rules, todayIso } = args;
  const out = new Map<string, TrainingStatus>();
  if (rules.newHireDays <= 0) return out;
  const experienced = new Set(rules.experienced);
  const cutoff = shiftCalendarDate(todayIso, "day", -rules.newHireDays);
  const dates = [...args.days.keys()].sort();
  const firstShift = new Map<string, string>();
  for (const iso of dates) {
    for (const p of args.days.get(iso)!) {
      if (!isOpenLane(p) && !firstShift.has(p.employee)) firstShift.set(p.employee, iso);
    }
  }
  for (const employee of args.employees) {
    if (experienced.has(employee)) continue;
    const hireDate = args.hireDates.get(employee) ?? null;
    const since = hireDate ?? firstShift.get(employee);
    if (!since || since < cutoff) continue;
    const t: TrainingStatus = {
      employee,
      hireDate,
      since,
      open: 0,
      close: 0,
      needOpen: rules.openShifts,
      needClose: rules.closeShifts,
      cleared: rules.openShifts <= 0 && rules.closeShifts <= 0,
      unsupervised: [],
    };
    for (const iso of dates) {
      if (t.cleared) break;
      const lanes = args.days.get(iso)!;
      const mine = lanes.find((p) => p.employee === employee && !isOpenLane(p));
      if (!mine) continue;
      const spans = spansOf(mine);
      const start = Math.min(...spans.map(([s]) => s));
      const end = Math.max(...spans.map(([, e]) => e));
      const withExperienced = lanes
        .filter((p) => !isOpenLane(p) && experienced.has(p.employee))
        .flatMap(spansOf);
      const together = spans.reduce((acc, [s, e]) => acc + overlapMinutes(s, e, withExperienced), 0);
      if (together < rules.overlapMin) {
        if (iso >= todayIso) t.unsupervised.push(iso);
        continue;
      }
      const kind = shiftKindOf(start, end);
      if (kind === "open") t.open += 1;
      if (kind === "close") t.close += 1;
      t.cleared = t.open >= t.needOpen && t.close >= t.needClose;
    }
    out.set(employee, t);
  }
  return out;
}
