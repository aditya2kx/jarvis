// Calendar-aware date-range presets shared by every Performance screen +
// Home (Issue #132 follow-up — replaces the old plain `?range=<days>` int).
// All "today" resolution happens once in America/Chicago via Intl; every
// other calculation below is pure y/m/d calendar arithmetic anchored to
// UTC-midnight Date objects (no time-of-day component), so it is immune to
// DST shifts — only the initial "what is today" lookup needs the timezone.

export type RangePreset = "7d" | "30d" | "this_week" | "this_month" | "last_week" | "last_month";

export const RANGE_PRESETS: { value: RangePreset; label: string }[] = [
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "this_week", label: "This week" },
  { value: "this_month", label: "This month" },
  { value: "last_week", label: "Last week" },
  { value: "last_month", label: "Last month" },
];

const PRESET_VALUES = new Set<string>(RANGE_PRESETS.map((p) => p.value));

export interface DateWindow {
  /** Inclusive lower bound, "YYYY-MM-DD". */
  start: string;
  /** Inclusive upper bound, "YYYY-MM-DD" — may be in the future for
   *  this_week/this_month (the calendar period isn't over yet). */
  end: string;
  label: string;
  preset: RangePreset;
}

/** 30d/this_month/last_month behave like "a month" for goal selection
 *  (Home scorecard picks the monthly vs weekly `store_config` goal by this). */
export function isMonthLike(preset: RangePreset): boolean {
  return preset === "30d" || preset === "this_month" || preset === "last_month";
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

function fmt(y: number, m: number, d: number): string {
  return `${y}-${pad2(m)}-${pad2(d)}`;
}

/** Calendar date as a UTC-midnight Date — arithmetic on this is DST-free
 *  because UTC has no DST; only reading "today" needs the real timezone. */
function toUTC(y: number, m: number, d: number): Date {
  return new Date(Date.UTC(y, m - 1, d));
}

function fromUTC(dt: Date): { y: number; m: number; d: number } {
  return { y: dt.getUTCFullYear(), m: dt.getUTCMonth() + 1, d: dt.getUTCDate() };
}

function addDays(y: number, m: number, d: number, days: number): { y: number; m: number; d: number } {
  const dt = toUTC(y, m, d);
  dt.setUTCDate(dt.getUTCDate() + days);
  return fromUTC(dt);
}

/** Today's calendar date in America/Chicago (the store's operating timezone). */
export function chicagoToday(): { y: number; m: number; d: number } {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Chicago",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value);
  return { y: get("year"), m: get("month"), d: get("day") };
}

/** Monday-start (ISO) week bounds for the week containing (y, m, d). */
function weekBounds(y: number, m: number, d: number): { start: { y: number; m: number; d: number }; end: { y: number; m: number; d: number } } {
  const utcDay = toUTC(y, m, d).getUTCDay(); // 0=Sun..6=Sat
  const daysSinceMonday = (utcDay + 6) % 7;
  const start = addDays(y, m, d, -daysSinceMonday);
  const end = addDays(start.y, start.m, start.d, 6);
  return { start, end };
}

// `m` need not be normalized to 1-12 by the caller — toUTC()'s underlying
// Date.UTC() rolls month 0 back to December of y-1 (needed for last_month
// in January, where the caller passes m=0), and month 13 forward likewise.
function monthBounds(y: number, m: number): { start: { y: number; m: number; d: number }; end: { y: number; m: number; d: number } } {
  const start = fromUTC(toUTC(y, m, 1));
  // Day 0 of next month == last day of this month.
  const end = fromUTC(toUTC(y, m + 1, 0));
  return { start, end };
}

/** Resolve a `?range=` search-param value (or an invalid/missing one) into
 *  an explicit [start, end] calendar window. Unknown values fall back to
 *  `fallback` (default "30d") rather than throwing — same permissive
 *  contract as the old `parseRange`. */
export function resolveRange(
  value: string | string[] | undefined,
  fallback: RangePreset = "30d",
): DateWindow {
  const raw = Array.isArray(value) ? value[0] : value;
  const preset: RangePreset = raw && PRESET_VALUES.has(raw) ? (raw as RangePreset) : fallback;
  const label = RANGE_PRESETS.find((p) => p.value === preset)!.label;
  const today = chicagoToday();

  switch (preset) {
    case "7d": {
      const start = addDays(today.y, today.m, today.d, -6);
      return { start: fmt(start.y, start.m, start.d), end: fmt(today.y, today.m, today.d), label, preset };
    }
    case "30d": {
      const start = addDays(today.y, today.m, today.d, -29);
      return { start: fmt(start.y, start.m, start.d), end: fmt(today.y, today.m, today.d), label, preset };
    }
    case "this_week": {
      const { start, end } = weekBounds(today.y, today.m, today.d);
      return { start: fmt(start.y, start.m, start.d), end: fmt(end.y, end.m, end.d), label, preset };
    }
    case "last_week": {
      const lastWeekAnchor = addDays(today.y, today.m, today.d, -7);
      const { start, end } = weekBounds(lastWeekAnchor.y, lastWeekAnchor.m, lastWeekAnchor.d);
      return { start: fmt(start.y, start.m, start.d), end: fmt(end.y, end.m, end.d), label, preset };
    }
    case "this_month": {
      const { start, end } = monthBounds(today.y, today.m);
      return { start: fmt(start.y, start.m, start.d), end: fmt(end.y, end.m, end.d), label, preset };
    }
    case "last_month": {
      // Month 0 == previous month (JS Date normalizes m=0 to Dec of y-1).
      const { start, end } = monthBounds(today.y, today.m - 1);
      return { start: fmt(start.y, start.m, start.d), end: fmt(end.y, end.m, end.d), label, preset };
    }
  }
}
