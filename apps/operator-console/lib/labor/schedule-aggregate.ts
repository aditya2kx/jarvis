import {
  concurrentFromRanges,
  parseShiftRangesJson,
} from "@/lib/labor/shift-ranges";
import {
  addGrain,
  truncateToGrain,
  type Grain,
} from "@/lib/filters/range";

export type ScheduledDayAgg = {
  date: string;
  parttime_hours: number;
  fulltime_hours: number;
  parttime_concurrent: number | null;
  fulltime_concurrent: number | null;
  total_concurrent: number | null;
};

type ShiftDayIn = {
  date: string;
  labor_bucket: string;
  scheduled_hours: number;
  shift_ranges_json: string | null;
};

/** Collapse employee-day schedule rows into per-calendar-day concurrent + hours.

 * Hours charts use paid `scheduled_hours`. Concurrent uses wall-clock hours
 * derived from shift ranges so a sparse scrape that over-allocated paid hours
 * onto few days cannot invent 2.4 FT concurrent for one person.
 */
export function aggregateScheduledDays(rows: ShiftDayIn[]): ScheduledDayAgg[] {
  type Acc = {
    ptH: number;
    ftH: number;
    ptRanges: ReturnType<typeof parseShiftRangesJson>;
    ftRanges: ReturnType<typeof parseShiftRangesJson>;
  };
  const byDate = new Map<string, Acc>();
  for (const r of rows) {
    const iso = String(r.date).slice(0, 10);
    let acc = byDate.get(iso);
    if (!acc) {
      acc = { ptH: 0, ftH: 0, ptRanges: [], ftRanges: [] };
      byDate.set(iso, acc);
    }
    const hrs = Number(r.scheduled_hours) || 0;
    const ranges = parseShiftRangesJson(r.shift_ranges_json);
    if (r.labor_bucket === "fulltime") {
      acc.ftH += hrs;
      acc.ftRanges.push(...ranges);
    } else {
      acc.ptH += hrs;
      acc.ptRanges.push(...ranges);
    }
  }
  const wallHrs = (ranges: ReturnType<typeof parseShiftRangesJson>) =>
    ranges.reduce((s, r) => s + r.hours, 0);
  return [...byDate.entries()]
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([date, acc]) => {
      const ptC = concurrentFromRanges(wallHrs(acc.ptRanges), acc.ptRanges);
      const ftC = concurrentFromRanges(wallHrs(acc.ftRanges), acc.ftRanges);
      const allRanges = [...acc.ptRanges, ...acc.ftRanges];
      const totalC = concurrentFromRanges(wallHrs(allRanges), allRanges);
      return {
        date,
        parttime_hours: Number(acc.ptH.toFixed(1)),
        fulltime_hours: Number(acc.ftH.toFixed(1)),
        parttime_concurrent: ptC,
        fulltime_concurrent: ftC,
        total_concurrent: totalC,
      };
    });
}

/** Average daily concurrent values into Aggregation buckets. */
export function rollConcurrentToGrain(
  days: ScheduledDayAgg[],
  grain: Grain,
): {
  date: string;
  parttime_concurrent: number | null;
  fulltime_concurrent: number | null;
  total_concurrent: number | null;
}[] {
  if (grain === "day") {
    return days.map((d) => ({
      date: d.date,
      parttime_concurrent: d.parttime_concurrent,
      fulltime_concurrent: d.fulltime_concurrent,
      total_concurrent: d.total_concurrent,
    }));
  }
  // Hour grain: scheduled concurrent needs range parsing — skip (empty).
  if (grain === "hour") {
    return [];
  }
  type Bucket = {
    pt: number[];
    ft: number[];
    tot: number[];
  };
  const map = new Map<string, Bucket>();
  for (const d of days) {
    const b = truncateToGrain(d.date, grain);
    let acc = map.get(b);
    if (!acc) {
      acc = { pt: [], ft: [], tot: [] };
      map.set(b, acc);
    }
    if (d.parttime_concurrent != null) acc.pt.push(d.parttime_concurrent);
    if (d.fulltime_concurrent != null) acc.ft.push(d.fulltime_concurrent);
    if (d.total_concurrent != null) acc.tot.push(d.total_concurrent);
  }
  const avg = (xs: number[]) =>
    xs.length ? Number((xs.reduce((a, b) => a + b, 0) / xs.length).toFixed(1)) : null;
  return [...map.entries()]
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([date, acc]) => ({
      date,
      parttime_concurrent: avg(acc.pt),
      fulltime_concurrent: avg(acc.ft),
      total_concurrent: avg(acc.tot),
    }));
}

/** Ensure weekday spine enumeration still advances (re-export helper for tests). */
export function nextBucket(iso: string, grain: Grain): string {
  return addGrain(iso, grain, 1);
}
