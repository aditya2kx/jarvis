/**
 * Pay-period calendar helpers matching agents/bhaga/scripts/update_model_sheet.py
 * `most_recent_closed_period` / open-window math (America/Chicago date).
 *
 * Used when `vw_model_payroll_period` has no `is_open=TRUE` rows yet (e.g. the
 * day after a period closes, before ADP shifts land and the model materializes
 * an open row). Tip exemptions still need an editable window for orphans.
 */

/** Palmetto store-profile: pay_periods_anchor_end_date / Biweekly. */
export const PALMETTO_ANCHOR_END = "2026-05-17";
export const PALMETTO_PERIOD_DAYS = 14;

function chicagoTodayIso(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Chicago",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

function parseIso(d: string): Date {
  const [y, m, day] = d.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, day));
}

function toIso(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function addDays(d: Date, n: number): Date {
  const out = new Date(d.getTime());
  out.setUTCDate(out.getUTCDate() + n);
  return out;
}

/** Most recent fully-elapsed closed period as of `todayIso` (YYYY-MM-DD). */
export function mostRecentClosedPeriod(
  todayIso: string,
  anchorEndIso = PALMETTO_ANCHOR_END,
  periodDays = PALMETTO_PERIOD_DAYS,
): { start: string; end: string } {
  const today = parseIso(todayIso);
  const anchorEnd = parseIso(anchorEndIso);
  const beforeToday = addDays(today, -1);
  const deltaDays = Math.floor(
    (beforeToday.getTime() - anchorEnd.getTime()) / 86_400_000,
  );
  const k = Math.floor(deltaDays / periodDays);
  const end = addDays(anchorEnd, periodDays * k);
  const start = addDays(end, -(periodDays - 1));
  return { start: toIso(start), end: toIso(end) };
}

/** Calendar open window: day after closed end through closed_end + periodDays. */
export function calendarOpenPayPeriod(
  todayIso = chicagoTodayIso(),
): { start: string; end: string } {
  const closed = mostRecentClosedPeriod(todayIso);
  const start = toIso(addDays(parseIso(closed.end), 1));
  const end = toIso(addDays(parseIso(closed.end), PALMETTO_PERIOD_DAYS));
  return { start, end };
}
