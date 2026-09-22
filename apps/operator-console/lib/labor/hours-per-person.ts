import type { LaborHoursChartRow } from "@/components/labor/LaborHoursChart";
import { LABOR_CHART_COLORS } from "@/lib/charts/palette";
import { showsFullTime, showsPartTime } from "@/lib/filters/labor-type";
import {
  enumerateBucketStarts,
  formatBucket,
  truncateToGrain,
  type DateWindow,
  type Grain,
} from "@/lib/filters/range";

/**
 * One person's hours on one day. The page already loads these for Staffing
 * coverage — clocked shifts over the actual window, schedule over the window
 * after the handoff — so both per-person charts are built from them rather
 * than from separate queries that could drift from the coverage view.
 */
export type PersonDayHours = {
  date: string;
  employee: string;
  labor_bucket: string;
  hours: number;
};

export type HoursPerPersonTooltipEntry = { label: string; value: string; color?: string };

export type HoursPerPersonChartRow = {
  employee: string;
  parttime: number | null;
  fulltime: number | null;
  parttime_sched: number | null;
  fulltime_sched: number | null;
  combined: number;
  tooltipEntries: HoursPerPersonTooltipEntry[];
};

export type HoursPerPersonSeries = { key: string; label: string; color: string };

type Bucket = "parttime" | "fulltime";

const ACTUAL_COLOR: Record<Bucket, string> = {
  parttime: LABOR_CHART_COLORS.parttimeActual,
  fulltime: LABOR_CHART_COLORS.fulltimeActual,
};
const SCHED_COLOR: Record<Bucket, string> = {
  parttime: LABOR_CHART_COLORS.parttimeScheduled,
  fulltime: LABOR_CHART_COLORS.fulltimeScheduled,
};

/** Labels match "Labor hours by …" so the charts read as one legend. */
const SERIES: HoursPerPersonSeries[] = [
  { key: "parttime", label: "Part-time", color: ACTUAL_COLOR.parttime },
  { key: "fulltime", label: "Full-time", color: ACTUAL_COLOR.fulltime },
  { key: "parttime_sched", label: "Part-time (scheduled)", color: SCHED_COLOR.parttime },
  { key: "fulltime_sched", label: "Full-time (scheduled)", color: SCHED_COLOR.fulltime },
];

function bucketOf(labor_bucket: string): Bucket {
  return labor_bucket === "fulltime" ? "fulltime" : "parttime";
}

/**
 * Half-up to 0.1 h. ADP hours are minutes / 60, so day sums land exactly on
 * .x5 (62.15) and float addition order can leave them a hair under — the
 * epsilon keeps a total from rounding differently depending on how it was summed.
 */
function round1(n: number): number {
  return Math.round(n * 10 + 1e-6) / 10;
}

function orNull(n: number): number | null {
  return n > 0 ? round1(n) : null;
}

function formatHours(n: number): string {
  return n.toLocaleString("en-US", { maximumFractionDigits: 1 });
}

type Split = { actual: Record<Bucket, number>; sched: Record<Bucket, number> };

function emptySplit(): Split {
  return { actual: { parttime: 0, fulltime: 0 }, sched: { parttime: 0, fulltime: 0 } };
}

function addRows(
  into: (row: PersonDayHours) => Split | null,
  rows: PersonDayHours[],
  kind: "actual" | "sched",
  allowed: Record<Bucket, boolean>,
): void {
  for (const r of rows) {
    const h = Number(r.hours) || 0;
    const bucket = bucketOf(r.labor_bucket);
    if (!(h > 0) || !allowed[bucket]) continue;
    const split = into(r);
    if (split) split[kind][bucket] += h;
  }
}

/**
 * Period total per person: clocked in the PT/FT actual colour, the
 * not-yet-clocked schedule stacked on top in the matching slate.
 *
 * Scheduled days after `periodEnd` are dropped. The page fetches the schedule
 * through the ADP horizon so the dated charts can show the days beyond the
 * Period, but a total with no date axis would silently count them.
 */
export function mergeHoursPerPerson(
  actual: PersonDayHours[],
  scheduled: PersonDayHours[],
  laborTypes: string[] | null,
  periodEnd: string,
): { rows: HoursPerPersonChartRow[]; series: HoursPerPersonSeries[] } {
  const allowed = { parttime: showsPartTime(laborTypes), fulltime: showsFullTime(laborTypes) };
  const people = new Map<string, Split>();
  const person = (r: PersonDayHours) => {
    let s = people.get(r.employee);
    if (!s) people.set(r.employee, (s = emptySplit()));
    return s;
  };
  addRows(person, actual, "actual", allowed);
  addRows(person, scheduled.filter((r) => r.date.slice(0, 10) <= periodEnd), "sched", allowed);

  const rows: HoursPerPersonChartRow[] = [...people.entries()].map(([employee, s]) => {
    const rawActual = s.actual.parttime + s.actual.fulltime;
    const rawSched = s.sched.parttime + s.sched.fulltime;
    const actualHrs = round1(rawActual);
    const schedHrs = round1(rawSched);
    const combined = round1(rawActual + rawSched);
    // A person has one bucket, but a mid-Period wage change could give two;
    // colour the tooltip by whichever holds more of their hours.
    const bucket: Bucket =
      s.actual.fulltime + s.sched.fulltime > s.actual.parttime + s.sched.parttime
        ? "fulltime"
        : "parttime";
    const tooltipEntries: HoursPerPersonTooltipEntry[] = [];
    if (actualHrs > 0) {
      tooltipEntries.push({ label: "Actual", value: formatHours(actualHrs), color: ACTUAL_COLOR[bucket] });
    }
    if (schedHrs > 0) {
      tooltipEntries.push({ label: "Scheduled", value: formatHours(schedHrs), color: SCHED_COLOR[bucket] });
    }
    if (actualHrs > 0 && schedHrs > 0) {
      tooltipEntries.push({ label: "Total (combined)", value: formatHours(combined) });
    }
    return {
      employee,
      parttime: orNull(s.actual.parttime),
      fulltime: orNull(s.actual.fulltime),
      parttime_sched: orNull(s.sched.parttime),
      fulltime_sched: orNull(s.sched.fulltime),
      combined,
      tooltipEntries,
    };
  });
  rows.sort((a, b) => b.combined - a.combined || a.employee.localeCompare(b.employee));

  const series = SERIES.filter((sr) =>
    rows.some((r) => r[sr.key as "parttime" | "fulltime" | "parttime_sched" | "fulltime_sched"] != null),
  );
  return { rows, series };
}

/** Hour of day needs clock-hour allocation of each shift; not built per person. */
export function personChartSupportsGrain(grain: Grain): boolean {
  return grain !== "hour";
}

/** Count of each weekday bucket in `win` — the divisor for Stat = Average. */
function weekdayCounts(win: DateWindow): Map<string, number> {
  const counts = new Map<string, number>();
  for (const day of enumerateBucketStarts(win, "day")) {
    const key = truncateToGrain(day, "weekday");
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return counts;
}

/**
 * One person's hours per Aggregation bucket, shaped for `LaborHoursChart` so
 * the single-person chart reads exactly like "Labor hours by …": same spine
 * (`win` = the page's chart window), same actual/scheduled handoff, same
 * stacks. Labor % is store-level, so it is always null here.
 */
export function personHoursByGrain(opts: {
  actual: PersonDayHours[];
  scheduled: PersonDayHours[];
  employee: string;
  grain: Grain;
  stat: "total" | "avg";
  win: DateWindow;
}): LaborHoursChartRow[] {
  const { grain, win } = opts;
  const all = { parttime: true, fulltime: true };
  const buckets = new Map<string, Split>();
  const bucket = (r: PersonDayHours) => {
    const day = r.date.slice(0, 10);
    if (r.employee !== opts.employee || day < win.start || day > win.end) return null;
    const key = truncateToGrain(day, grain);
    let s = buckets.get(key);
    if (!s) buckets.set(key, (s = emptySplit()));
    return s;
  };
  addRows(bucket, opts.actual, "actual", all);
  addRows(bucket, opts.scheduled, "sched", all);

  const divisors = opts.stat === "avg" && grain === "weekday" ? weekdayCounts(win) : null;
  return enumerateBucketStarts(win, grain).map((iso) => {
    const s = buckets.get(iso) ?? emptySplit();
    const div = divisors?.get(iso) || 1;
    const pt = orNull(s.actual.parttime / div);
    const ft = orNull(s.actual.fulltime / div);
    return {
      date: formatBucket(iso, grain, grain === "day" ? { weekday: true } : undefined),
      bucket_iso: iso,
      total_hours: orNull((s.actual.parttime + s.actual.fulltime) / div),
      parttime_hours: pt,
      fulltime_hours: ft,
      labor_pct: null,
      hourly_pct: null,
      fulltime_pct: null,
      net_sales: null,
      parttime_scheduled_hours: orNull(s.sched.parttime / div),
      fulltime_scheduled_hours: orNull(s.sched.fulltime / div),
    };
  });
}
