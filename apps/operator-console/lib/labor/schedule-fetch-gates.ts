/**
 * Labor page: chart schedule stacks vs staffing-coverage swimlanes.
 * Coverage is day-level — Aggregation must not gate scheduled segments.
 */

/** Hours / Concurrent schedule stacks (omit on Hour grain; require today in Period). */
export function showChartSchedule(opts: {
  includesToday: boolean;
  hasSchedWin: boolean;
  grain: string;
}): boolean {
  return opts.includesToday && opts.hasSchedWin && opts.grain !== "hour";
}

/**
 * Open-shift stack on the Hours chart (Issue #342): follows the schedule stack,
 * and also skips Weekday, where one open slot would be blended across weeks.
 */
export function showChartOpenShifts(opts: {
  includesToday: boolean;
  hasSchedWin: boolean;
  grain: string;
}): boolean {
  return showChartSchedule(opts) && opts.grain !== "weekday";
}

/** Staffing coverage: any Aggregation; any Period with a non-null schedule window. */
export function showCoverageSchedule(opts: { hasSchedWin: boolean }): boolean {
  return opts.hasSchedWin;
}
