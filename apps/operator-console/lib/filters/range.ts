// Calendar-aware date-range presets shared by every Performance screen +
// Home (Issue #132 follow-up — replaces the old plain `?range=<days>` int).
// All "today" resolution happens once in America/Chicago via Intl; every
// other calculation below is pure y/m/d calendar arithmetic anchored to
// UTC-midnight Date objects (no time-of-day component), so it is immune to
// DST shifts — only the initial "what is today" lookup needs the timezone.

export type RangePreset =
  | "7d"
  | "30d"
  | "this_week"
  | "this_month"
  | "last_week"
  | "last_month"
  | "custom";

export const RANGE_PRESETS: { value: RangePreset; label: string }[] = [
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "this_week", label: "This week" },
  { value: "this_month", label: "This month" },
  { value: "last_week", label: "Last week" },
  { value: "last_month", label: "Last month" },
  { value: "custom", label: "Custom…" },
];

/** Cookie keeps Period aligned across Home / Sales / Labor / Accounting / … */
export const PERIOD_COOKIE = "oc_range";

/** Build a nav href that preserves the current period preset. */
export function periodHref(
  basePath: string,
  preset: RangePreset,
  extra: Record<string, string> = {},
): string {
  const params = new URLSearchParams({ ...extra, range: preset });
  return `${basePath}?${params.toString()}`;
}

const PRESET_VALUES = new Set<string>(RANGE_PRESETS.map((p) => p.value));

/** "YYYY-MM-DD" shape check — cheap guard before trusting a raw search param
 *  as a SQL date bound (still passed through `dateParam()` downstream, but
 *  this keeps an obviously-malformed value from silently becoming "Invalid
 *  Date" -> NaN comparisons in the resolved window). */
const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function isValidIsoDate(s: string | undefined): s is string {
  if (!s || !ISO_DATE_RE.test(s)) return false;
  const d = new Date(`${s}T00:00:00Z`);
  return !Number.isNaN(d.getTime());
}

function firstValue(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

/** True as soon as the user has picked "Custom…" from the Period dropdown,
 *  even before they've chosen valid `from`/`to` dates. Distinct from
 *  `resolveRange(...).preset === "custom"`, which only reports "custom" once
 *  a valid window exists — pages use this to keep the DateRangePicker (and
 *  its underlying <input type="date"> fields) visible the moment "Custom…"
 *  is selected, rather than only after a window has already been chosen. */
export function wantsCustom(value: string | string[] | undefined): boolean {
  return firstValue(value) === "custom";
}

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

export function chicagoTodayIso(): string {
  const t = chicagoToday();
  return fmt(t.y, t.m, t.d);
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
 *  contract as the old `parseRange`. `from`/`to` (both required, both valid
 *  "YYYY-MM-DD", `from` <= `to`) are only consulted when the resolved preset
 *  is "custom"; any other combination (missing, malformed, non-custom
 *  preset, or `from` after `to`) falls back to `fallback` rather than
 *  silently producing an inverted or NaN window. */
export function resolveRange(
  value: string | string[] | undefined,
  fallback: RangePreset = "30d",
  from?: string | string[] | undefined,
  to?: string | string[] | undefined,
): DateWindow {
  const raw = firstValue(value);
  let preset: RangePreset = raw && PRESET_VALUES.has(raw) ? (raw as RangePreset) : fallback;

  if (preset === "custom") {
    const f = firstValue(from);
    const t = firstValue(to);
    if (!isValidIsoDate(f) || !isValidIsoDate(t) || f > t) {
      preset = fallback === "custom" ? "30d" : fallback;
    } else {
      return { start: f, end: t, label: "Custom", preset: "custom" };
    }
  }

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

// ── Aggregation grain (Issue #132 follow-up) ────────────────────────────────
// Every Performance reader groups by this grain server-side (BigQuery
// GROUP BY <bucketSql(grain)>), never client-side — so a "month" bucket sums
// the exact same underlying rows a "day" bucket would show individually.

export type Grain = "day" | "week" | "month";

export const GRAINS: { value: Grain; label: string }[] = [
  { value: "day", label: "Daily" },
  { value: "week", label: "Weekly" },
  { value: "month", label: "Monthly" },
];

const GRAIN_VALUES = new Set<string>(GRAINS.map((g) => g.value));

export function parseGrain(value: string | string[] | undefined, fallback: Grain = "day"): Grain {
  const raw = firstValue(value);
  return raw && GRAIN_VALUES.has(raw) ? (raw as Grain) : fallback;
}

// `grain` is never string-interpolated from a request — it is parsed above
// into one of exactly 3 literal TS union values, then this function maps
// that closed set to one of exactly 3 hardcoded SQL fragments. There is no
// code path from raw user input to a SQL string here (see queries.ts
// `bucketSql` usages — always `bucketSql(grain)` on a `Grain`-typed value,
// never a template of the raw search-param).
export function bucketSql(grain: Grain, dateCol = "date"): string {
  switch (grain) {
    case "day":
      return dateCol;
    case "week":
      return `DATE_TRUNC(${dateCol}, WEEK(MONDAY))`;
    case "month":
      return `DATE_TRUNC(${dateCol}, MONTH)`;
  }
}

const MONTH_ABBR = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/** Render a bucketed date value the way each grain reads best: a day as
 *  "Jun 30", a week as "Wk of Jun 30" (Monday, matching `bucketSql`'s
 *  WEEK(MONDAY) truncation), a month as "Jan 2026".
 *
 *  Deliberately does NOT go through `new Date(str)` + an America/Chicago
 *  `Intl.DateTimeFormat` (the pattern `formatDate` in lib/format.ts uses for
 *  TIMESTAMP columns) — a bucketed DATE value has no time-of-day/timezone
 *  component to begin with (America/Chicago is already baked in by however
 *  the underlying `date`/`date_local` column was written), so round-tripping
 *  it through UTC-midnight parsing + Chicago-timezone rendering silently
 *  shifts the calendar date backward — for a month bucket like "2026-01-01"
 *  that's not a cosmetic one-day slip, it renders the wrong MONTH entirely
 *  ("Dec 2025"). Parsing y/m/d directly from the ISO string avoids that. */
export function formatBucket(value: string | Date | null | undefined, grain: Grain): string {
  if (!value) return "—";
  const iso = typeof value === "string" ? value : value.toISOString();
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return "—";
  const year = m[1];
  const month = MONTH_ABBR[Number(m[2]) - 1];
  const day = Number(m[3]);
  if (!month) return "—";
  if (grain === "month") return `${month} ${year}`;
  const dayLabel = `${month} ${day}`;
  return grain === "week" ? `Wk of ${dayLabel}` : dayLabel;
}
