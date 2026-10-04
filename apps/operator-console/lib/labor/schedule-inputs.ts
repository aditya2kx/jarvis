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

export type StaffRuleKind = "target_week_hours" | "max_shifts_per_period" | "last_day";

export type StaffRule = {
  id: string;
  employee: string;
  kind: StaffRuleKind;
  value: number;
  /** `last_day` only: final working day (YYYY-MM-DD); never drafted after it. */
  date?: string;
};

export type StaffLimits = { targetWeekHours?: number; maxShiftsPerPeriod?: number; lastDay?: string };

/** Store-wide basics behind the needed headcount and the draft. */
export type StaffingBasics = {
  /** Orders one person handles in an hour; need = ⌈typical orders ÷ this⌉. */
  ordersPerPerson: number;
  /** Fewest people on the floor while staffed. */
  minPeople: number;
  /** Staffed window (first clock-in → end of closing duties), minute of day. */
  openMin: number;
  closeMin: number;
  /** Shortest shift the draft proposes. */
  minShiftMin: number;
};

export type ScheduleRules = { staffing: StaffingBasics; dayRules: DayRule[]; staffRules: StaffRule[] };

/** One saved, immutable version of a store's rules (labor_schedule_rules row). */
export type RulesVersion = {
  version: number;
  rules: ScheduleRules;
  /** False for versions saved before staffing basics existed (they load the defaults). */
  savedStaffing: boolean;
  note: string | null;
  createdBy: string;
  createdAt: string;
};

/** ADP's shortest regular shift is 4.5 h (4:00–8:30 PM closer). */
export const DEFAULT_STAFFING: StaffingBasics = {
  ordersPerPerson: 4,
  minPeople: 1,
  openMin: 6 * 60 + 30,
  closeMin: 20 * 60 + 30,
  minShiftMin: 270,
};

export const DEFAULT_RULES: ScheduleRules = {
  staffing: DEFAULT_STAFFING,
  dayRules: [
    {
      id: "closing-duties",
      scope: "all",
      exceptDelivery: false,
      fromMin: 19 * 60 + 30,
      toMin: 20 * 60 + 30,
      people: 2,
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
export function staffLimits(rules: StaffRule[]): Map<string, StaffLimits> {
  const out = new Map<string, StaffLimits>();
  for (const r of rules) {
    if (!r.employee) continue;
    const cur = out.get(r.employee) ?? {};
    if (r.kind === "last_day") {
      if (r.date && ISO_DATE.test(r.date)) cur.lastDay = r.date;
    } else if (!(r.value >= 0)) continue;
    else if (r.kind === "target_week_hours") cur.targetWeekHours = r.value;
    else cur.maxShiftsPerPeriod = r.value;
    out.set(r.employee, cur);
  }
  return out;
}

const SCOPES = new Set<string>(["weekdays", "weekends", "all", "delivery", "0", "1", "2", "3", "4", "5", "6"]);
const KINDS = new Set<string>(["target_week_hours", "max_shifts_per_period", "last_day"]);
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const isMin = (v: unknown): v is number => Number.isInteger(v) && (v as number) >= 0 && (v as number) <= 24 * 60;

/** Validate untrusted JSON (a saved version or a client save) into ScheduleRules; throws on bad shape. */
export function parseScheduleRules(raw: unknown): ScheduleRules {
  const obj = (typeof raw === "string" ? JSON.parse(raw) : raw) as Partial<ScheduleRules> | null;
  if (!obj || !Array.isArray(obj.dayRules) || !Array.isArray(obj.staffRules)) {
    throw new Error("Rules must have dayRules and staffRules lists");
  }
  const dayRules = obj.dayRules.map((r, i): DayRule => {
    if (!SCOPES.has(String(r?.scope)) || !isMin(r.fromMin) || !isMin(r.toMin) || r.toMin <= r.fromMin) {
      throw new Error(`Day rule ${i + 1}: needs a valid day and a from time before the to time`);
    }
    if (!Number.isInteger(r.people) || r.people < 0 || r.people > 20) {
      throw new Error(`Day rule ${i + 1}: people must be a whole number 0–20`);
    }
    return {
      id: String(r.id || `day-${i}`),
      scope: r.scope,
      exceptDelivery: Boolean(r.exceptDelivery),
      fromMin: r.fromMin,
      toMin: r.toMin,
      people: r.people,
    };
  });
  const staffRules = obj.staffRules.map((r, i): StaffRule => {
    if (!r?.employee || !KINDS.has(String(r.kind))) {
      throw new Error(`Staff rule ${i + 1}: needs an employee and a rule type`);
    }
    const base = { id: String(r.id || `staff-${i}`), employee: String(r.employee), kind: r.kind };
    if (r.kind === "last_day") {
      if (!ISO_DATE.test(String(r.date ?? ""))) throw new Error(`Staff rule ${i + 1}: last day needs a date`);
      return { ...base, value: 0, date: String(r.date) };
    }
    if (!(Number(r.value) >= 0) || Number(r.value) > 80) {
      throw new Error(`Staff rule ${i + 1}: value must be 0–80`);
    }
    return { ...base, value: Number(r.value) };
  });
  return { staffing: parseStaffing(obj.staffing), dayRules, staffRules };
}

/** Versions saved before staffing basics existed load with the defaults. */
function parseStaffing(raw: unknown): StaffingBasics {
  if (raw == null) return DEFAULT_STAFFING;
  const s = raw as Partial<StaffingBasics>;
  const opp = Number(s.ordersPerPerson);
  if (!(opp >= 1 && opp <= 30)) throw new Error("Orders per person must be between 1 and 30");
  if (!Number.isInteger(s.minPeople) || s.minPeople! < 0 || s.minPeople! > 10) {
    throw new Error("Minimum people must be a whole number 0–10");
  }
  if (!isMin(s.openMin) || !isMin(s.closeMin) || s.closeMin <= s.openMin) {
    throw new Error("Staffed hours need a start before the end");
  }
  if (!Number.isInteger(s.minShiftMin) || s.minShiftMin! < 60 || s.minShiftMin! > 12 * 60) {
    throw new Error("Shortest shift must be 1–12 hours");
  }
  return {
    ordersPerPerson: opp,
    minPeople: s.minPeople!,
    openMin: s.openMin,
    closeMin: s.closeMin,
    minShiftMin: s.minShiftMin!,
  };
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
