/**
 * First-month hours cap (Issue #539): for the month from someone's first ADP shift, the draft
 * gives them at most `StaffingBasics.firstMonthWeekHours` a week (ADP scheduled + draft). Counted
 * from the first shift, not the hire date; only people whose first shift is on or after
 * `FIRST_MONTH_FROM` — everyone earlier was already working when the rule started.
 */

import { shiftCalendarDate } from "@/lib/filters/range";
import { isOpenLane, type CoveragePersonDay } from "@/lib/labor/coverage-model";
import type { Availability } from "@/lib/labor/shift-draft";

export const FIRST_MONTH_FROM = "2026-11-01";

export type FirstMonth = {
  employee: string;
  /** First ADP shift (clocked or scheduled); null = none yet, so their first drafted shift starts it. */
  firstShift: string | null;
  /** Day after the first month ends (first shift + 1 calendar month); null while no shift yet. */
  until: string | null;
};

/**
 * Everyone on `employees` still in (or not yet started) their first month on `todayIso`. `days`
 * holds every ADP lane since well before `FIRST_MONTH_FROM`; empty (history not loaded) = nobody,
 * so a failed read never caps the whole roster.
 */
export function firstMonths(args: {
  todayIso: string;
  employees: readonly string[];
  days: ReadonlyMap<string, CoveragePersonDay[]>;
}): Map<string, FirstMonth> {
  const out = new Map<string, FirstMonth>();
  if (!args.days.size) return out;
  const first = new Map<string, string>();
  for (const iso of [...args.days.keys()].sort()) {
    for (const p of args.days.get(iso)!) {
      if (!isOpenLane(p) && !first.has(p.employee)) first.set(p.employee, iso);
    }
  }
  for (const employee of args.employees) {
    const firstShift = first.get(employee) ?? null;
    if (firstShift != null && firstShift < FIRST_MONTH_FROM) continue;
    const until = firstShift && shiftCalendarDate(firstShift, "month", 1);
    if (until && until <= args.todayIso) continue;
    out.set(employee, { employee, firstShift, until });
  }
  return out;
}

/**
 * Start the month of anyone without an ADP shift at their first drafted shift in `byDay` (one
 * drafted week), so later weeks stop capping them a month on. Mutates `months`.
 */
export function startDraftedMonths(
  months: Map<string, FirstMonth>,
  byDay: ReadonlyMap<string, readonly { employee: string | null }[]>,
): void {
  for (const iso of [...byDay.keys()].sort()) {
    for (const s of byDay.get(iso)!) {
      const m = s.employee ? months.get(s.employee) : undefined;
      if (m && m.firstShift == null) {
        months.set(m.employee, { ...m, firstShift: iso, until: shiftCalendarDate(iso, "month", 1) });
      }
    }
  }
}

/**
 * The week starting `weekStart` (Monday) with first-month people capped at `weekHours` — any week
 * that starts before their first month ends. An hour target above the cap comes down to it.
 */
export function capFirstMonth(
  roster: Availability[],
  weekStart: string,
  months: ReadonlyMap<string, FirstMonth>,
  weekHours: number | null,
): Availability[] {
  if (weekHours == null || !months.size) return roster;
  return roster.map((a) => {
    const m = months.get(a.employee);
    if (!m || (m.until && weekStart >= m.until)) return a;
    return {
      ...a,
      maxWeekHours: Math.min(a.maxWeekHours, weekHours),
      ...(a.targetWeekHours != null ? { targetWeekHours: Math.min(a.targetWeekHours, weekHours) } : {}),
      firstMonthUntil: m.until ?? undefined,
      firstMonthCap: weekHours,
    };
  });
}
