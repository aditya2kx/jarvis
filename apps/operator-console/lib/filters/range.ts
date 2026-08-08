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
/** Aggregation grain shared across Performance pages that expose Aggregation. */
export const GRAIN_COOKIE = "oc_grain";
/** Custom Period bounds — used when PERIOD_COOKIE is `custom`. */
export const FROM_COOKIE = "oc_from";
export const TO_COOKIE = "oc_to";

const COOKIE_MAX_AGE = 31536000; // 1y

/** Client-side cookie write used by Period / Aggregation / custom Apply. */
export function writeFilterCookie(name: string, value: string): void {
  if (typeof document === "undefined") return;
  document.cookie = `${name}=${encodeURIComponent(value)};path=/;max-age=${COOKIE_MAX_AGE};SameSite=Lax`;
}

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
// `weekday` collapses the Period onto Mon…Sun (all Mondays together, etc.).
// `all` collapses the entire Period into one bucket (Issue #225).

export type Grain = "day" | "week" | "month" | "weekday" | "hour" | "all";

export const GRAINS: { value: Grain; label: string }[] = [
  { value: "day", label: "Daily" },
  { value: "week", label: "Weekly" },
  { value: "month", label: "Monthly" },
  { value: "weekday", label: "Weekday" },
  { value: "hour", label: "Hour of day" },
  { value: "all", label: "Entire period" },
];

/** Date-only Aggregation options — omit Hour (Accounting / Order Quality). */
export const GRAINS_WITHOUT_HOUR: { value: Grain; label: string }[] = GRAINS.filter(
  (g) => g.value !== "hour",
);

/** Operator-facing grain noun for chart titles / table captions. */
export function grainDisplayLabel(grain: Grain): string {
  switch (grain) {
    case "day":
      return "day";
    case "week":
      return "week";
    case "month":
      return "month";
    case "weekday":
      return "weekday";
    case "hour":
      return "hour";
    case "all":
      return "period";
  }
}

/** Capitalized Aggregation adjective — matches GRAINS[].label (Daily / Weekday / …). */
export function grainTitleLabel(grain: Grain): string {
  return GRAINS.find((g) => g.value === grain)?.label ?? "Daily";
}

const GRAIN_VALUES = new Set<string>(GRAINS.map((g) => g.value));

export function parseGrain(value: string | string[] | undefined, fallback: Grain = "day"): Grain {
  const raw = firstValue(value);
  return raw && GRAIN_VALUES.has(raw) ? (raw as Grain) : fallback;
}

/**
 * Compare-prior window: each Aggregation bucket in `win` shifted back by one
 * `compareGrain` step (previous day / week / month). Display Aggregation and
 * Compare lag are independent — e.g. day grain + previous week overlays each
 * day against the same weekday last week (Issue #202 follow-on).
 *
 * When `compareGrain` is omitted it defaults to `displayGrain` (legacy lag-1).
 * Weekday / Hour-of-day Aggregation uses an equal-length prior Period (same
 * span shifted back), so Mon…Sun / 0–23 bars still align by index.
 * Entire-period Aggregation likewise shifts the whole Period back by its length.
 */
export function priorWindow(
  win: DateWindow,
  displayGrain: Grain = "day",
  compareGrain: Grain = displayGrain,
): DateWindow {
  if (displayGrain === "weekday" || displayGrain === "hour" || displayGrain === "all") {
    const m0 = /^(\d{4})-(\d{2})-(\d{2})/.exec(win.start);
    const m1 = /^(\d{4})-(\d{2})-(\d{2})/.exec(win.end);
    if (m0 && m1) {
      const startUtc = toUTC(Number(m0[1]), Number(m0[2]), Number(m0[3]));
      const endUtc = toUTC(Number(m1[1]), Number(m1[2]), Number(m1[3]));
      const days =
        Math.round((endUtc.getTime() - startUtc.getTime()) / 86_400_000) + 1;
      return {
        start: shiftCalendarDate(win.start, "day", -days),
        end: shiftCalendarDate(win.end, "day", -days),
        label: "Prior period",
        preset: "custom",
      };
    }
  }
  const curBuckets = enumerateBucketStarts(win, displayGrain);
  if (curBuckets.length === 0) {
    const lagStep: Grain =
      compareGrain === "weekday" || compareGrain === "hour" || compareGrain === "all"
        ? "day"
        : compareGrain;
    return {
      start: shiftCalendarDate(win.start, lagStep, -1),
      end: shiftCalendarDate(win.end, lagStep, -1),
      label: "Prior period",
      preset: "custom",
    };
  }
  const lagGrain: Grain =
    compareGrain === "weekday" || compareGrain === "hour" || compareGrain === "all"
      ? "week"
      : compareGrain;
  const priorBuckets = curBuckets.map((b) =>
    truncateToGrain(shiftCalendarDate(b, lagGrain, -1), displayGrain),
  );
  return {
    start: priorBuckets[0]!,
    end: grainEndInclusive(priorBuckets[priorBuckets.length - 1]!, displayGrain),
    label: "Prior period",
    preset: "custom",
  };
}

/** Inclusive last calendar day of a truncated grain bucket. */
export function grainEndInclusive(bucketStart: string, grain: Grain): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(bucketStart);
  if (!m) return bucketStart;
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const d = Number(m[3]);
  if (grain === "day" || grain === "weekday" || grain === "hour" || grain === "all") {
    return bucketStart;
  }
  if (grain === "week") {
    const end = addDays(y, mo, d, 6);
    return fmt(end.y, end.m, end.d);
  }
  const end = fromUTC(toUTC(y, mo + 1, 0));
  return fmt(end.y, end.m, end.d);
}

/** Shift a calendar YYYY-MM-DD by one or more grain steps (not truncated first). */
export function shiftCalendarDate(isoDate: string, grain: Grain, delta: number): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(isoDate);
  if (!m) return isoDate;
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const d = Number(m[3]);
  if (grain === "day" || grain === "weekday" || grain === "hour" || grain === "all") {
    // Weekday lag is "same DOW previous week" when used as a step of 1 → 7 days.
    // Hour anchors step by calendar day (hour index); entire-period is day-stepped.
    const step = grain === "weekday" ? delta * 7 : delta;
    const next = addDays(y, mo, d, step);
    return fmt(next.y, next.m, next.d);
  }
  if (grain === "week") {
    const next = addDays(y, mo, d, delta * 7);
    return fmt(next.y, next.m, next.d);
  }
  // month — clamp day to last day of target month (Mar 31 - 1mo → Feb 28/29)
  const last = fromUTC(toUTC(y, mo + delta + 1, 0));
  const day = Math.min(d, last.d);
  const next = fromUTC(toUTC(y, mo + delta, day));
  return fmt(next.y, next.m, next.d);
}

/** Weekday Aggregation anchors: Mon 1970-01-05 … Sun 1970-01-11. */
export const WEEKDAY_ANCHOR_MON = "1970-01-05";

/**
 * Hour-of-day Aggregation anchors: hour 0 → 1970-01-01 … hour 23 → 1970-01-24.
 * (Same DATE-anchor trick as weekday — charts keep a DATE x-key.)
 */
export const HOUR_ANCHOR_START = "1970-01-01";

/** Entire-period Aggregation anchor — one GROUP BY key for the whole Period. */
export const ALL_PERIOD_ANCHOR = "1970-01-01";

/** Map hour 0–23 → DATE anchor ISO. */
export function hourAnchorIso(hour: number): string {
  const h = Math.max(0, Math.min(23, Math.trunc(hour)));
  const next = addDays(1970, 1, 1, h);
  return fmt(next.y, next.m, next.d);
}

/** Inverse of `hourAnchorIso` — days since 1970-01-01 clamped to 0–23. */
export function hourIndexFromAnchor(isoDate: string): number {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(isoDate);
  if (!m) return 0;
  const utc = toUTC(Number(m[1]), Number(m[2]), Number(m[3]));
  const base = toUTC(1970, 1, 1);
  const days = Math.round((utc.getTime() - base.getTime()) / 86_400_000);
  return Math.max(0, Math.min(23, days));
}

/** `12am` … `11pm` from hour 0–23. */
export function formatHourLabel(hour: number): string {
  const h = ((Math.trunc(hour) % 24) + 24) % 24;
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12}${h < 12 ? "am" : "pm"}`;
}

/**
 * Whitelisted SQL fragment mapping `hour_local` (0–23) onto hour DATE anchors.
 * `hourCol` must be a closed identifier from our queries — never user input.
 */
export function hourBucketSql(hourCol = "hour_local"): string {
  if (!/^[a-z_][a-z0-9_]*$/i.test(hourCol)) {
    throw new Error(`hourBucketSql: invalid hour column ${hourCol}`);
  }
  return `DATE_ADD(DATE '${HOUR_ANCHOR_START}', INTERVAL ${hourCol} DAY)`;
}

/** Inclusive list of truncated bucket ISO starts covering `win` at `grain`. */
export function enumerateBucketStarts(win: DateWindow, grain: Grain): string[] {
  // Weekday Aggregation always yields Mon…Sun anchors (Period only filters which
  // underlying days contribute — empty DOWs simply have no BQ rows).
  if (grain === "weekday") {
    return Array.from({ length: 7 }, (_, i) => addGrain(WEEKDAY_ANCHOR_MON, "weekday", i));
  }
  // Hour of day always yields 24 anchors 0…23 (Period filters contributing days).
  if (grain === "hour") {
    return Array.from({ length: 24 }, (_, i) => hourAnchorIso(i));
  }
  if (grain === "all") {
    return [ALL_PERIOD_ANCHOR];
  }
  const out: string[] = [];
  let cur = truncateToGrain(win.start, grain);
  const end = win.end;
  // Safety cap — a year of days is plenty for console ranges.
  for (let i = 0; i < 400 && cur <= end; i++) {
    out.push(cur);
    cur = addGrain(cur, grain, 1);
  }
  return out;
}

/** Advance (or rewind) a truncated bucket ISO by `delta` grain steps. */
export function addGrain(isoDate: string, grain: Grain, delta: number): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(isoDate);
  if (!m) return isoDate;
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const d = Number(m[3]);
  if (grain === "day") {
    const next = addDays(y, mo, d, delta);
    return fmt(next.y, next.m, next.d);
  }
  if (grain === "all") {
    return ALL_PERIOD_ANCHOR;
  }
  if (grain === "weekday") {
    const next = addDays(y, mo, d, delta);
    return truncateToGrain(fmt(next.y, next.m, next.d), "weekday");
  }
  if (grain === "hour") {
    const next = addDays(y, mo, d, delta);
    return truncateToGrain(fmt(next.y, next.m, next.d), "hour");
  }
  if (grain === "week") {
    const next = addDays(y, mo, d, delta * 7);
    return truncateToGrain(fmt(next.y, next.m, next.d), "week");
  }
  // month
  const dt = toUTC(y, mo + delta, 1);
  return fmt(dt.getUTCFullYear(), dt.getUTCMonth() + 1, 1);
}

// `grain` is never string-interpolated from a request — it is parsed above
// into one of the literal TS union values, then this function maps that closed
// set to a hardcoded SQL fragment. There is no code path from raw user input
// to a SQL string here (see queries.ts `bucketSql` usages — always
// `bucketSql(grain)` on a `Grain`-typed value, never a template of the raw
// search-param).
//
// Weekday buckets are anchored to Mon 1970-01-05 … Sun 1970-01-11 so ORDER BY
// date yields Mon→Sun and `formatBucket` can render weekday names.
// Hour-of-day uses `hourBucketSql` (not this function) — DATE columns cannot
// express clock hour; Sales special-cases grain===hour.
// Entire-period buckets are anchored to ALL_PERIOD_ANCHOR so one GROUP BY
// key covers the full Period window.
export function bucketSql(grain: Grain, dateCol = "date"): string {
  switch (grain) {
    case "day":
      return dateCol;
    case "week":
      return `DATE_TRUNC(${dateCol}, WEEK(MONDAY))`;
    case "month":
      return `DATE_TRUNC(${dateCol}, MONTH)`;
    case "weekday":
      // BQ DAYOFWEEK: Sunday=1 … Saturday=7 → Mon=0 … Sun=6
      return `DATE_ADD(DATE '${WEEKDAY_ANCHOR_MON}', INTERVAL MOD(EXTRACT(DAYOFWEEK FROM ${dateCol}) + 5, 7) DAY)`;
    case "hour":
      throw new Error(
        "bucketSql(hour) is unsupported — use hourBucketSql(hourCol) (Sales-only)",
      );
    case "all":
      return `DATE '${ALL_PERIOD_ANCHOR}'`;
  }
}

const MONTH_ABBR = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/** Two-letter weekday for dense chart axes (Su…Sa). Calendar date via UTC. */
const WEEKDAY_2 = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"] as const;

/** Monday-first long labels for Aggregation=weekday. */
const WEEKDAY_LONG = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
] as const;

/** Mon=0 … Sun=6 from a YYYY-MM-DD calendar date (UTC). */
export function weekdayIndexMon0(isoDate: string): number {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(isoDate);
  if (!m) return 0;
  const utc = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  return (utc.getUTCDay() + 6) % 7;
}

/** Render a bucketed date value the way each grain reads best: a day as
 *  "Jun 30", a week as "Wk of Jun 30" (Monday, matching `bucketSql`'s
 *  WEEK(MONDAY) truncation), a month as "Jan 2026", a weekday as "Monday",
 *  entire period as "Entire period".
 *
 *  Deliberately does NOT go through `new Date(str)` + an America/Chicago
 *  `Intl.DateTimeFormat` (the pattern `formatDate` in lib/format.ts uses for
 *  TIMESTAMP columns) — a bucketed DATE value has no time-of-day/timezone
 *  component to begin with (America/Chicago is already baked in by however
 *  the underlying `date`/`date_local` column was written), so round-tripping
 *  it through UTC-midnight parsing + Chicago-timezone rendering silently
 *  shifts the calendar date backward — for a month bucket like "2026-01-01"
 *  that's not a cosmetic one-day slip, it renders the wrong MONTH entirely
 *  ("Dec 2025"). Parsing y/m/d directly from the ISO string avoids that.
 *
 *  When `weekday: true` and grain is `day`, appends a second line with a
 *  two-letter weekday (`"Jun 30\\nMo"`) for Labor day-of-week goals without
 *  lengthening the primary date token. */
export function formatBucket(
  value: string | Date | null | undefined,
  grain: Grain,
  opts?: { weekday?: boolean },
): string {
  if (!value) return "—";
  if (grain === "all") return "Entire period";
  const iso = typeof value === "string" ? value : value.toISOString();
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return "—";
  const year = m[1];
  const monthNum = Number(m[2]);
  const month = MONTH_ABBR[monthNum - 1];
  const day = Number(m[3]);
  if (!month) return "—";
  if (grain === "weekday") {
    return WEEKDAY_LONG[weekdayIndexMon0(`${year}-${m[2]}-${m[3]}`)] ?? "—";
  }
  if (grain === "hour") {
    return formatHourLabel(hourIndexFromAnchor(`${year}-${m[2]}-${m[3]}`));
  }
  if (grain === "month") return `${month} ${year}`;
  const dayLabel = `${month} ${day}`;
  if (grain === "week") return `Wk of ${dayLabel}`;
  if (opts?.weekday) {
    const dow = WEEKDAY_2[new Date(Date.UTC(Number(year), monthNum - 1, day)).getUTCDay()];
    return `${dayLabel}\n${dow}`;
  }
  return dayLabel;
}

/** Client-side mirror of `bucketSql` — ISO date truncated to day / week(Mon) / month / weekday-anchor / hour-anchor / entire-period. */
export function truncateToGrain(isoDate: string, grain: Grain): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(isoDate));
  if (!m) return String(isoDate).slice(0, 10);
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const d = Number(m[3]);
  if (grain === "all") return ALL_PERIOD_ANCHOR;
  if (grain === "day") return `${m[1]}-${m[2]}-${m[3]}`;
  if (grain === "month") return `${m[1]}-${m[2]}-01`;
  if (grain === "weekday") {
    const idx = weekdayIndexMon0(`${m[1]}-${m[2]}-${m[3]}`);
    const next = addDays(1970, 1, 5, idx);
    return fmt(next.y, next.m, next.d);
  }
  if (grain === "hour") {
    // Calendar dates have no hour — clamp onto the 0–23 anchor spine by day-of-month
    // offset from HOUR_ANCHOR_START when already an hour anchor, else hour 0.
    const idx = hourIndexFromAnchor(`${m[1]}-${m[2]}-${m[3]}`);
    // Real calendar dates (e.g. 2026-07-06) are far from 1970 → clamp to 23;
    // only 1970-01-01…24 are meaningful hour anchors. Detect via year.
    if (y === 1970 && mo === 1 && d >= 1 && d <= 24) {
      return hourAnchorIso(d - 1);
    }
    return HOUR_ANCHOR_START;
  }
  const utc = new Date(Date.UTC(y, mo - 1, d));
  const daysSinceMonday = (utc.getUTCDay() + 6) % 7;
  utc.setUTCDate(utc.getUTCDate() - daysSinceMonday);
  return utc.toISOString().slice(0, 10);
}
