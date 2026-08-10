import {
  chicagoTodayIso,
  shiftCalendarDate,
  type DateWindow,
  type Grain,
} from "@/lib/filters/range";

/** Period overlaps Chicago today (schedule overlay is eligible). */
export function periodIncludesToday(win: DateWindow, todayIso = chicagoTodayIso()): boolean {
  return win.start <= todayIso && win.end >= todayIso;
}

/**
 * Collapsing Aggregations (Weekday / Hour of day) average finished days only.
 * Schedule stacks on those charts mix unfinished ADP shifts into Mon…Sun /
 * hour-of-day rollups and mislead — keep schedule on day/week/month (+ coverage).
 */
export function scheduleStacksOnLaborCharts(grain: Grain): boolean {
  return grain !== "weekday" && grain !== "hour";
}

/**
 * Actual punches window: through yesterday only when Period reaches today+.
 * Returns null when the Period is entirely today-or-later.
 */
export function actualPunchWindow(
  win: DateWindow,
  todayIso = chicagoTodayIso(),
): DateWindow | null {
  if (win.start >= todayIso) return null;
  const yesterday = shiftCalendarDate(todayIso, "day", -1);
  const end = win.end < todayIso ? win.end : yesterday;
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
 * Scheduled shifts window: from Chicago today through Period end, extended to
 * the ADP schedule horizon when Period includes today.
 * Returns null when the Period ends before today.
 */
export function scheduledShiftWindow(
  win: DateWindow,
  todayIso = chicagoTodayIso(),
  scheduleHorizonEnd: string | null = null,
): DateWindow | null {
  if (win.end < todayIso) return null;
  const start = win.start > todayIso ? win.start : todayIso;
  const end = periodIncludesToday(win, todayIso)
    ? extendEndForScheduleHorizon(win.end, scheduleHorizonEnd)
    : win.end;
  if (start > end) return null;
  return { ...win, start, end, preset: "custom", label: win.label };
}
