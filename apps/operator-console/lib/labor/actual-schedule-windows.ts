import {
  chicagoTodayIso,
  shiftCalendarDate,
  type DateWindow,
} from "@/lib/filters/range";

/** Period overlaps Chicago today (schedule overlay is eligible). */
export function periodIncludesToday(win: DateWindow, todayIso = chicagoTodayIso()): boolean {
  return win.start <= todayIso && win.end >= todayIso;
}

/**
 * First day scheduled hours may stand in for actuals.
 *
 * "Tomorrow" once today's punches have landed, otherwise today — never earlier.
 * The split has to follow the data, not the calendar: hours are ingested each
 * evening, so from then until midnight a day has real clocked hours *and* a
 * schedule, and picking the schedule means drawing a forecast over a fact. On
 * 2026-09-13 that showed the finished week of Sep 7 as 184.3 combined hours
 * (152.9 actual + 31.4 scheduled) when the truth already in BQ was 181.3, with
 * Sunday's 28.3 clocked hours hidden behind its 31.4-hour schedule.
 *
 * Clamped at today so a lagging ingest cannot pull the handoff backwards and
 * paint schedule over days that are simply awaiting their punches.
 */
export function scheduleTakesOverFrom(
  todayIso: string,
  actualsThroughIso: string | null | undefined,
): string {
  if (!actualsThroughIso) return todayIso;
  const dayAfter = shiftCalendarDate(actualsThroughIso, "day", 1);
  return dayAfter > todayIso ? dayAfter : todayIso;
}

/**
 * Actual punches window: through the day before `boundaryIso` when the Period
 * reaches it. Returns null when the Period is entirely boundary-or-later.
 */
export function actualPunchWindow(
  win: DateWindow,
  boundaryIso = chicagoTodayIso(),
): DateWindow | null {
  if (win.start >= boundaryIso) return null;
  const lastActualDay = shiftCalendarDate(boundaryIso, "day", -1);
  const end = win.end < boundaryIso ? win.end : lastActualDay;
  if (end < win.start) return null;
  return { ...win, start: win.start, end, preset: "custom", label: win.label };
}

/**
 * End of schedule fetch / chart spine when Period includes today:
 * max(Period end, latest ADP scheduled date), else Period end.
 */
export function extendEndForScheduleHorizon(
  periodEnd: string,
  scheduleHorizonEnd: string | null | undefined,
): string {
  if (!scheduleHorizonEnd) return periodEnd;
  return scheduleHorizonEnd > periodEnd ? scheduleHorizonEnd : periodEnd;
}

/**
 * Chart spine window: when Period includes today, extend through available
 * scheduled shifts (any Aggregation). Past-only Periods are unchanged.
 */
export function laborChartWindow(
  win: DateWindow,
  todayIso = chicagoTodayIso(),
  scheduleHorizonEnd: string | null = null,
): DateWindow {
  if (!periodIncludesToday(win, todayIso)) return win;
  const end = extendEndForScheduleHorizon(win.end, scheduleHorizonEnd);
  if (end === win.end) return win;
  return { ...win, end, preset: "custom", label: win.label };
}

/**
 * Scheduled shifts window: from `boundaryIso` through Period end, extended to
 * the ADP schedule horizon when the Period reaches it.
 *
 * Returns null when the Period ends before the boundary — which is the case
 * every evening once the day's punches land: nothing in the Period is still a
 * forecast, so no schedule is drawn.
 */
export function scheduledShiftWindow(
  win: DateWindow,
  boundaryIso = chicagoTodayIso(),
  scheduleHorizonEnd: string | null = null,
): DateWindow | null {
  if (win.end < boundaryIso) return null;
  const start = win.start > boundaryIso ? win.start : boundaryIso;
  const end = periodIncludesToday(win, boundaryIso)
    ? extendEndForScheduleHorizon(win.end, scheduleHorizonEnd)
    : win.end;
  if (start > end) return null;
  return { ...win, start, end, preset: "custom", label: win.label };
}

const ISO = /^\d{4}-\d{2}-\d{2}$/;

/**
 * Timecard scrape target: past coverage chip or closed period end, else yesterday.
 * Never today+ (incomplete punches).
 */
export function clockedHoursTargetDate(opts: {
  todayIso: string;
  periodEnd?: string | null;
  coverageDay?: string | null;
}): string {
  const yesterday = shiftCalendarDate(opts.todayIso, "day", -1);
  const day = (opts.coverageDay ?? "").slice(0, 10);
  if (ISO.test(day) && day < opts.todayIso) return day;
  const end = (opts.periodEnd ?? "").slice(0, 10);
  if (ISO.test(end) && end < opts.todayIso) return end;
  return yesterday;
}
