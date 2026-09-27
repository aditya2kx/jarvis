/**
 * Operator rules that shape the needed headcount + draft (Issue #337).
 * Day rules replace the computed need inside their window (later rules win);
 * staff rules steer who gets draft shifts.
 */

import { isoWeekdayMon0 } from "@/lib/labor/staffing-need";

/** "weekdays" | "weekends" | "all" | "delivery" | "0".."6" (Mon = 0). */
export type DayScope = "weekdays" | "weekends" | "all" | "delivery" | `${0 | 1 | 2 | 3 | 4 | 5 | 6}`;

export type DayRule = {
  id: string;
  scope: DayScope;
  /** Skip frozen delivery days (ignored when scope is "delivery"). */
  exceptDelivery: boolean;
  fromMin: number;
  toMin: number;
  people: number;
};

export type StaffRuleKind = "target_week_hours" | "max_shifts_per_period";

export type StaffRule = {
  id: string;
  employee: string;
  kind: StaffRuleKind;
  value: number;
};

export type ScheduleRules = { dayRules: DayRule[]; staffRules: StaffRule[] };

export const DEFAULT_RULES: ScheduleRules = {
  dayRules: [
    {
      id: "weekday-open",
      scope: "weekdays",
      exceptDelivery: true,
      fromMin: 6 * 60 + 30,
      toMin: 8 * 60 + 30,
      people: 1,
    },
  ],
  staffRules: [],
};

export function dayRuleApplies(rule: DayRule, iso: string, deliveryDates: ReadonlySet<string>): boolean {
  const day = iso.slice(0, 10);
  const isDelivery = deliveryDates.has(day);
  if (rule.scope === "delivery") return isDelivery;
  if (rule.exceptDelivery && isDelivery) return false;
  const dow = isoWeekdayMon0(day);
  if (rule.scope === "all") return true;
  if (rule.scope === "weekdays") return dow < 5;
  if (rule.scope === "weekends") return dow >= 5;
  return Number(rule.scope) === dow;
}

export function applyDayRules(
  iso: string,
  mins: number[],
  need: number[],
  dayRules: DayRule[],
  deliveryDates: ReadonlySet<string>,
): number[] {
  const rules = dayRules.filter((r) => r.toMin > r.fromMin && dayRuleApplies(r, iso, deliveryDates));
  return need.map((n, i) => {
    const t = mins[i]!;
    const r = rules.findLast((x) => t >= x.fromMin && t < x.toMin);
    return r ? Math.max(0, r.people) : n;
  });
}

/** Per-employee limits derived from staff rules (last rule of a kind wins). */
export function staffLimits(
  rules: StaffRule[],
): Map<string, { targetWeekHours?: number; maxShiftsPerPeriod?: number }> {
  const out = new Map<string, { targetWeekHours?: number; maxShiftsPerPeriod?: number }>();
  for (const r of rules) {
    if (!r.employee || !(r.value >= 0)) continue;
    const cur = out.get(r.employee) ?? {};
    if (r.kind === "target_week_hours") cur.targetWeekHours = r.value;
    else cur.maxShiftsPerPeriod = r.value;
    out.set(r.employee, cur);
  }
  return out;
}

/** "HH:MM" (24h, from <input type="time">) ↔ minute-of-day. */
export function timeToMin(v: string): number | null {
  const m = /^(\d{2}):(\d{2})$/.exec(v);
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}

export function minToTime(min: number | null): string {
  if (min == null) return "";
  return `${String(Math.floor(min / 60)).padStart(2, "0")}:${String(min % 60).padStart(2, "0")}`;
}
