/** Mon–Sun week choices for per-week pickers. */

import { shiftCalendarDate } from "@/lib/filters/range";
import { isoWeekdayMon0 } from "@/lib/labor/staffing-need";

export type WeekOption = { start: string; range: string; label: string; current: boolean };

export function weekStartOf(iso: string): string {
  return shiftCalendarDate(iso.slice(0, 10), "day", -isoWeekdayMon0(iso.slice(0, 10)));
}

function md(iso: string): { m: string; d: number } {
  const [y, mo, d] = iso.split("-").map(Number);
  return { m: new Date(y!, mo! - 1, d!).toLocaleDateString("en-US", { month: "short" }), d: d! };
}

/** "Oct 5–11" or "Sep 28–Oct 4". */
export function weekRange(start: string): string {
  const a = md(start);
  const b = md(shiftCalendarDate(start, "day", 6));
  return a.m === b.m ? `${a.m} ${a.d}–${b.d}` : `${a.m} ${a.d}–${b.m} ${b.d}`;
}

/** Every week touched by `dates` or `range`, plus this week, newest first. */
export function weekOptions(
  dates: string[],
  todayIso: string,
  range?: { start: string; end: string },
): WeekOption[] {
  const current = weekStartOf(todayIso);
  const next = shiftCalendarDate(current, "day", 7);
  const spanned: string[] = [];
  if (range) {
    for (let w = weekStartOf(range.start); w <= range.end; w = shiftCalendarDate(w, "day", 7)) spanned.push(w);
  }
  const starts = [...new Set([...dates.map(weekStartOf), ...spanned, current])].sort().reverse();
  return starts.map((start) => {
    const range = weekRange(start);
    const tag = start === current ? "This week · " : start === next ? "Next week · " : "";
    return { start, range, label: `${tag}${range}`, current: start === current };
  });
}

export function inWeeks(date: string, starts: ReadonlySet<string>): boolean {
  return starts.has(weekStartOf(date));
}
