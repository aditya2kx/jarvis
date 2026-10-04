/**
 * Where a week's drafted hours come from, so the operator can see which
 * staffing basic or day rule to loosen when the week runs over the goal.
 *
 * Required person-hours are split in rule order: first the minimum on the
 * floor, then each day rule's change on top of everything before it (later
 * rules win, so a rule that lowers need shows negative hours). Shift shape is
 * what the drafted shifts add beyond those person-hours (shortest-shift
 * padding, template blocks, people already on the floor).
 */

import { applyDayRules, type DayRule } from "@/lib/labor/schedule-inputs";

export type RuleHours = { id: string; hours: number };

export type HoursBreakdown = {
  /** Already in ADP (clocked or scheduled). */
  existing: number;
  /** Minimum on the floor across staffed hours. */
  floor: number;
  rules: RuleHours[];
  /** Draft hours for required coverage beyond the person-hours above. */
  shape: number;
  /** Extra shifts for busy order times, within the weekly goal. */
  peak: number;
};

const STEP_H = 0.25;

/** Person-hours needed beyond who is already on the floor. */
export function shortHours(need: readonly number[], cover: readonly number[]): number {
  return need.reduce((a, n, i) => a + Math.max(0, n - (cover[i] ?? 0)) * STEP_H, 0);
}

/** One day's required person-hours: floor first, then each day rule in order. */
export function requiredByRule(args: {
  iso: string;
  mins: number[];
  floorOnly: number[];
  cover: readonly number[];
  dayRules: DayRule[];
  deliveries: ReadonlySet<string>;
}): { floor: number; rules: RuleHours[] } {
  const floor = shortHours(args.floorOnly, args.cover);
  let prev = floor;
  const rules = args.dayRules.map((r, k) => {
    const need = applyDayRules(args.iso, args.mins, args.floorOnly, args.dayRules.slice(0, k + 1), args.deliveries);
    const cur = shortHours(need, args.cover);
    const hours = cur - prev;
    prev = cur;
    return { id: r.id, hours };
  });
  return { floor, rules };
}

export function emptyBreakdown(existing: number, ruleIds: readonly string[]): HoursBreakdown {
  return { existing, floor: 0, rules: ruleIds.map((id) => ({ id, hours: 0 })), shape: 0, peak: 0 };
}

/** Add one day's required split and its required-pass draft hours into the week. */
export function addDay(
  b: HoursBreakdown,
  day: { floor: number; rules: RuleHours[] },
  requiredDraftHours: number,
): void {
  b.floor += day.floor;
  day.rules.forEach((r, i) => (b.rules[i]!.hours += r.hours));
  b.shape += requiredDraftHours - day.floor - day.rules.reduce((a, r) => a + r.hours, 0);
}
