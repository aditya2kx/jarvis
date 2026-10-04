/**
 * Operator rules that shape the needed headcount + draft (Issue #337).
 * Day rules replace the computed need inside their window (later rules win);
 * staff rules steer who gets draft shifts.
 */

import { isoWeekdayMon0 } from "@/lib/labor/staffing-need";

/** How a day rule treats frozen delivery days. */
export type DeliveryMode = "any" | "only" | "skip";

export const ALL_DAYS = [0, 1, 2, 3, 4, 5, 6] as const;

export type DayRule = {
  id: string;
  /** Weekdays the rule covers, Mon = 0 … Sun = 6 (sorted, at least one). */
  days: number[];
  delivery: DeliveryMode;
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
      days: [...ALL_DAYS],
      delivery: "any",
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
  if (rule.delivery === "only" && !isDelivery) return false;
  if (rule.delivery === "skip" && isDelivery) return false;
  return rule.days.includes(isoWeekdayMon0(day));
}

export type DayRuleConflict = { a: string; b: string; days: number[]; fromMin: number; toMin: number };

/** Pairs of day rules that would both claim the same day and minute (an "only" and a "skip" delivery rule never share a day). */
export function dayRuleConflicts(rules: readonly DayRule[]): DayRuleConflict[] {
  const out: DayRuleConflict[] = [];
  rules.forEach((a, i) => {
    for (const b of rules.slice(i + 1)) {
      const modes = new Set([a.delivery, b.delivery]);
      if (modes.has("only") && modes.has("skip")) continue;
      const days = a.days.filter((d) => b.days.includes(d));
      const fromMin = Math.max(a.fromMin, b.fromMin);
      const toMin = Math.min(a.toMin, b.toMin);
      if (days.length && fromMin < toMin) out.push({ a: a.id, b: b.id, days, fromMin, toMin });
    }
  });
  return out;
}

export type UncoveredGroup = { days: number[]; windows: [number, number][] };
export type UncoveredWindows = { deliveryDays: boolean; groups: UncoveredGroup[] };

/**
 * Staffed time no day rule covers (the order-based need applies there), weekdays
 * with identical gaps grouped. Delivery days get their own list only when a rule
 * treats them differently.
 */
export function uncoveredWindows(rules: readonly DayRule[], staffing: StaffingBasics): UncoveredWindows[] {
  const forKind = (deliveryDays: boolean): UncoveredGroup[] => {
    const skip: DeliveryMode = deliveryDays ? "skip" : "only";
    const groups = new Map<string, UncoveredGroup>();
    for (const d of ALL_DAYS) {
      const covering = rules.filter((r) => r.delivery !== skip && r.days.includes(d));
      let gaps: [number, number][] = [[staffing.openMin, staffing.closeMin]];
      for (const r of covering) {
        gaps = gaps.flatMap(([s, e]): [number, number][] =>
          r.toMin <= s || r.fromMin >= e
            ? [[s, e]]
            : [
                ...(r.fromMin > s ? [[s, r.fromMin] as [number, number]] : []),
                ...(r.toMin < e ? [[r.toMin, e] as [number, number]] : []),
              ],
        );
      }
      const key = JSON.stringify(gaps);
      const g = groups.get(key) ?? groups.set(key, { days: [], windows: gaps }).get(key)!;
      g.days.push(d);
    }
    return [...groups.values()];
  };
  const regular = forKind(false);
  if (!rules.some((r) => r.delivery !== "any")) return [{ deliveryDays: false, groups: regular }];
  const delivery = forKind(true);
  const same = JSON.stringify(delivery) === JSON.stringify(regular);
  return same
    ? [{ deliveryDays: false, groups: regular }]
    : [
        { deliveryDays: false, groups: regular },
        { deliveryDays: true, groups: delivery },
      ];
}

/** "Every day", "Weekdays", "Weekends" or "Mon, Wed, Fri". */
export function daysLabel(days: readonly number[]): string {
  const key = [...days].sort().join("");
  if (key === "0123456") return "Every day";
  if (key === "01234") return "Weekdays";
  if (key === "56") return "Weekends";
  return [...days].sort().map((d) => DAY_NAMES[d]).join(", ");
}

export const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;

/** Versions saved before multi-day rules stored one `scope` + `exceptDelivery`. */
const LEGACY_SCOPE_DAYS: Record<string, number[]> = {
  all: [...ALL_DAYS],
  delivery: [...ALL_DAYS],
  weekdays: [0, 1, 2, 3, 4],
  weekends: [5, 6],
  ...Object.fromEntries(ALL_DAYS.map((d) => [String(d), [d]])),
};

function parseDays(r: { days?: unknown; scope?: unknown }): number[] | null {
  if (Array.isArray(r.days)) {
    const days = [...new Set(r.days)].filter((d): d is number => Number.isInteger(d) && d >= 0 && d <= 6);
    return days.length && days.length === r.days.length ? days.sort() : null;
  }
  return LEGACY_SCOPE_DAYS[String(r.scope)] ?? null;
}

function parseDelivery(r: { delivery?: unknown; scope?: unknown; exceptDelivery?: unknown }): DeliveryMode | null {
  if (r.delivery !== undefined) return DELIVERY_MODES.has(String(r.delivery)) ? (r.delivery as DeliveryMode) : null;
  if (r.scope === "delivery") return "only";
  return r.exceptDelivery ? "skip" : "any";
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

const DELIVERY_MODES = new Set<string>(["any", "only", "skip"]);
const KINDS = new Set<string>(["target_week_hours", "max_shifts_per_period", "last_day"]);
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const isMin = (v: unknown): v is number => Number.isInteger(v) && (v as number) >= 0 && (v as number) <= 24 * 60;

/** Validate untrusted JSON (a saved version or a client save) into ScheduleRules; throws on bad shape. */
export function parseScheduleRules(raw: unknown): ScheduleRules {
  const obj = (typeof raw === "string" ? JSON.parse(raw) : raw) as Partial<ScheduleRules> | null;
  if (!obj || !Array.isArray(obj.dayRules) || !Array.isArray(obj.staffRules)) {
    throw new Error("Rules must have dayRules and staffRules lists");
  }
  const dayRules = (obj.dayRules as unknown[]).map((raw, i): DayRule => {
    const r = (raw ?? {}) as Record<string, unknown> & Partial<DayRule>;
    const days = parseDays(r);
    const delivery = parseDelivery(r);
    if (!days || !delivery) throw new Error(`Day rule ${i + 1}: pick at least one day`);
    if (!isMin(r.fromMin) || !isMin(r.toMin) || r.toMin <= r.fromMin) {
      throw new Error(`Day rule ${i + 1}: needs a from time before the to time`);
    }
    if (!Number.isInteger(r.people) || r.people! < 0 || r.people! > 20) {
      throw new Error(`Day rule ${i + 1}: people must be a whole number 0–20`);
    }
    return {
      id: String(r.id || `day-${i}`),
      days,
      delivery,
      fromMin: r.fromMin,
      toMin: r.toMin,
      people: r.people!,
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
