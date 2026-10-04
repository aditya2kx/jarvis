/**
 * Roll the coverage panel's per-day draft values (hours, or average people on
 * the floor) into chart buckets the same way the server rolls up actuals.
 */

import { shiftCalendarDate, truncateToGrain, type Grain } from "@/lib/filters/range";

/**
 * Sum each day's value into its bucket; with `average`, divide by how many of
 * the Period's days fall in that bucket (e.g. Mondays for the weekday view).
 * Days outside the chart's buckets are dropped. Hour grain has no draft.
 */
export function bucketDaily(
  byDay: ReadonlyMap<string, number>,
  buckets: ReadonlySet<string>,
  grain: Grain,
  average: boolean,
  win: { start: string; end: string },
): Map<string, number> {
  const out = new Map<string, number>();
  if (grain === "hour" || !byDay.size) return out;
  for (const [iso, v] of byDay) {
    const b = truncateToGrain(iso, grain);
    if (buckets.has(b)) out.set(b, (out.get(b) ?? 0) + v);
  }
  if (!average) return out;
  const days = new Map<string, number>();
  for (let d = win.start; d <= win.end; d = shiftCalendarDate(d, "day", 1)) {
    const b = truncateToGrain(d, grain);
    days.set(b, (days.get(b) ?? 0) + 1);
  }
  for (const [b, v] of out) out.set(b, v / Math.max(1, days.get(b) ?? 1));
  return out;
}
