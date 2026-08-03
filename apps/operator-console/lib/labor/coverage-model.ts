/**
 * Day-strip + occupancy + swimlane model for Labor coverage (Issue #213 Option 1).
 */

import { parseShiftRangesJson } from "@/lib/labor/shift-ranges";
import { shiftCalendarDate, type DateWindow } from "@/lib/filters/range";
import { showsFullTime, showsPartTime } from "@/lib/filters/labor-type";

export type CoverageKind = "actual" | "scheduled";

export type CoverageSegment = {
  kind: CoverageKind;
  startMin: number;
  endMin: number;
  hours: number;
};

export type CoveragePersonDay = {
  employee: string;
  labor_bucket: string;
  segments: CoverageSegment[];
};

export type CoverageDayChip = {
  date: string;
  headcount: number;
  kind: CoverageKind | "mixed" | "empty";
};

export type OccupancyPoint = {
  min: number;
  actual: number;
  scheduled: number;
};

const HH_MM_RE = /^(\d{1,2}):(\d{2})$/;

/** Parse ADP punch `HH:MM` (24h) to minute-of-day. */
export function parseHhMm(s: string | null | undefined): number | null {
  if (!s) return null;
  const m = HH_MM_RE.exec(String(s).trim());
  if (!m) return null;
  const h = Number(m[1]);
  const min = Number(m[2]);
  if (h > 23 || min > 59) return null;
  return h * 60 + min;
}

export function formatClockMin(min: number): string {
  const m = ((min % (24 * 60)) + 24 * 60) % (24 * 60);
  const h24 = Math.floor(m / 60);
  const mm = m % 60;
  const ampm = h24 >= 12 ? "PM" : "AM";
  const h12 = h24 % 12 === 0 ? 12 : h24 % 12;
  return mm === 0 ? `${h12} ${ampm}` : `${h12}:${String(mm).padStart(2, "0")} ${ampm}`;
}

/** Inclusive calendar days in [start, end]. */
function enumerateInclusiveDays(startIso: string, endIso: string): string[] {
  const out: string[] = [];
  let cur = startIso.slice(0, 10);
  const end = endIso.slice(0, 10);
  if (cur > end) return out;
  while (cur <= end) {
    out.push(cur);
    cur = shiftCalendarDate(cur, "day", 1);
  }
  return out;
}

/**
 * Day-level strip covering the full Period window (inclusive).
 * When the Labor page passes `laborChartWindow` (Period + schedule horizon
 * if today is in Period), future scheduled days appear here too — same as the
 * hours / concurrent charts. No 7-day truncation; UI scrolls horizontally.
 */
export function coverageStripDates(
  win: Pick<DateWindow, "start" | "end">,
  _maxDaysIgnored?: number,
  _todayIsoIgnored?: string,
): string[] {
  return enumerateInclusiveDays(win.start, win.end);
}

/**
 * Default focus: yesterday if in strip, else today if in strip, else last chip.
 */
export function defaultCoverageDay(
  strip: string[],
  todayIso: string,
): string | null {
  if (!strip.length) return null;
  const yesterday = shiftCalendarDate(todayIso, "day", -1);
  if (strip.includes(yesterday)) return yesterday;
  if (strip.includes(todayIso)) return todayIso;
  return strip[strip.length - 1]!;
}

export function resolveCoverageDay(
  requested: string | undefined,
  strip: string[],
  todayIso: string,
): string | null {
  if (requested && strip.includes(requested.slice(0, 10))) {
    return requested.slice(0, 10);
  }
  return defaultCoverageDay(strip, todayIso);
}

function bucketAllowed(bucket: string, laborTypes: string[] | null): boolean {
  if (bucket === "parttime") return showsPartTime(laborTypes);
  if (bucket === "fulltime") return showsFullTime(laborTypes);
  return true;
}

export type ActualShiftInput = {
  date: string;
  employee: string;
  labor_bucket: string;
  in_time: string | null;
  out_time: string | null;
  total_hours: number;
};

export type ScheduledShiftInput = {
  date: string;
  employee: string;
  labor_bucket: string;
  scheduled_hours: number;
  shift_ranges_json: string | null;
};

function mergePerson(
  map: Map<string, CoveragePersonDay>,
  employee: string,
  labor_bucket: string,
  seg: CoverageSegment,
): void {
  const key = employee;
  const existing = map.get(key);
  if (!existing) {
    map.set(key, { employee, labor_bucket, segments: [seg] });
    return;
  }
  existing.segments.push(seg);
}

/** Build person→segments for one ISO day (actual punches + scheduled ranges). */
export function buildPersonDaysForDate(
  date: string,
  actuals: ActualShiftInput[],
  scheduled: ScheduledShiftInput[],
  laborTypes: string[] | null,
): CoveragePersonDay[] {
  const map = new Map<string, CoveragePersonDay>();

  for (const r of actuals) {
    if (r.date.slice(0, 10) !== date) continue;
    if (!bucketAllowed(r.labor_bucket, laborTypes)) continue;
    let start = parseHhMm(r.in_time);
    let end = parseHhMm(r.out_time);
    if (start == null || end == null) continue;
    if (end < start) end += 24 * 60;
    const hours =
      Number(r.total_hours) > 0
        ? Number(Number(r.total_hours).toFixed(2))
        : Number(((end - start) / 60).toFixed(2));
    mergePerson(map, r.employee, r.labor_bucket, {
      kind: "actual",
      startMin: start,
      endMin: end,
      hours,
    });
  }

  for (const r of scheduled) {
    if (r.date.slice(0, 10) !== date) continue;
    if (!bucketAllowed(r.labor_bucket, laborTypes)) continue;
    const ranges = parseShiftRangesJson(r.shift_ranges_json);
    if (!ranges.length) continue;
    for (const range of ranges) {
      mergePerson(map, r.employee, r.labor_bucket, {
        kind: "scheduled",
        startMin: range.startMin,
        endMin: range.endMin,
        hours: range.hours,
      });
    }
  }

  return [...map.values()].sort((a, b) => {
    const aStart = Math.min(...a.segments.map((s) => s.startMin));
    const bStart = Math.min(...b.segments.map((s) => s.startMin));
    if (aStart !== bStart) return aStart - bStart;
    return a.employee.localeCompare(b.employee);
  });
}

export function dayChipSummary(
  date: string,
  people: CoveragePersonDay[],
): CoverageDayChip {
  if (!people.length) return { date, headcount: 0, kind: "empty" };
  const hasA = people.some((p) => p.segments.some((s) => s.kind === "actual"));
  const hasS = people.some((p) => p.segments.some((s) => s.kind === "scheduled"));
  const kind: CoverageDayChip["kind"] =
    hasA && hasS ? "mixed" : hasA ? "actual" : hasS ? "scheduled" : "empty";
  return { date, headcount: people.length, kind };
}

/** Axis bounds from segments, padded to hour edges; fallback 9:00–21:00. */
export function axisBounds(
  people: CoveragePersonDay[],
  padMin = 30,
): { startMin: number; endMin: number } {
  let start = Infinity;
  let end = -Infinity;
  for (const p of people) {
    for (const s of p.segments) {
      start = Math.min(start, s.startMin);
      end = Math.max(end, s.endMin);
    }
  }
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
    return { startMin: 9 * 60, endMin: 21 * 60 };
  }
  start = Math.max(0, Math.floor((start - padMin) / 60) * 60);
  end = Math.min(24 * 60 + 12 * 60, Math.ceil((end + padMin) / 60) * 60);
  if (end <= start) end = start + 60;
  return { startMin: start, endMin: end };
}

function covers(seg: CoverageSegment, t: number): boolean {
  return seg.startMin <= t && t < seg.endMin;
}

/** Headcount at each step (unique people with any covering segment of that kind). */
export function occupancySeries(
  people: CoveragePersonDay[],
  startMin: number,
  endMin: number,
  stepMin = 15,
): OccupancyPoint[] {
  const out: OccupancyPoint[] = [];
  for (let t = startMin; t < endMin; t += stepMin) {
    let actual = 0;
    let scheduled = 0;
    for (const p of people) {
      if (p.segments.some((s) => s.kind === "actual" && covers(s, t))) actual += 1;
      if (p.segments.some((s) => s.kind === "scheduled" && covers(s, t))) {
        scheduled += 1;
      }
    }
    out.push({ min: t, actual, scheduled });
  }
  return out;
}

/**
 * Plain-English step summary for the dominant series (actual if any, else scheduled).
 * e.g. "1 until 11 AM, then 2 until 2 PM, then 1 until 8 PM"
 */
export function coverageNarrative(points: OccupancyPoint[]): string {
  if (!points.length) return "No coverage in range.";
  const useActual = points.some((p) => p.actual > 0);
  const series = points.map((p) => ({
    min: p.min,
    count: useActual ? p.actual : p.scheduled,
  }));
  const steps: { count: number; from: number; to: number }[] = [];
  for (const p of series) {
    const last = steps[steps.length - 1];
    if (last && last.count === p.count) {
      last.to = p.min;
    } else {
      steps.push({ count: p.count, from: p.min, to: p.min });
    }
  }
  // Extend last step to end of final bucket (~+step).
  if (steps.length && series.length) {
    const step = series.length > 1 ? series[1]!.min - series[0]!.min : 15;
    steps[steps.length - 1]!.to = series[series.length - 1]!.min + step;
  }
  const nonzero = steps.filter((s) => s.count > 0);
  if (!nonzero.length) return "No one on the floor.";
  return nonzero
    .map((s, i) => {
      const until = formatClockMin(s.to);
      if (i === 0) return `${s.count} until ${until}`;
      return `then ${s.count} until ${until}`;
    })
    .join(", ");
}

export function segmentLeftPct(
  seg: CoverageSegment,
  axisStart: number,
  axisSpan: number,
): number {
  if (!(axisSpan > 0)) return 0;
  return Math.max(0, ((seg.startMin - axisStart) / axisSpan) * 100);
}

export function segmentWidthPct(
  seg: CoverageSegment,
  axisStart: number,
  axisSpan: number,
): number {
  if (!(axisSpan > 0)) return 0;
  const left = Math.max(seg.startMin, axisStart);
  const right = Math.min(seg.endMin, axisStart + axisSpan);
  return Math.max(0, ((right - left) / axisSpan) * 100);
}

export type ActivePersonAt = {
  employee: string;
  labor_bucket: string;
  kind: CoverageKind;
  startMin: number;
  endMin: number;
  hours: number;
};

/** People whose shift covers minute `t` (prefer actual segment when both overlap). */
export function peopleActiveAt(
  people: CoveragePersonDay[],
  t: number,
): ActivePersonAt[] {
  const out: ActivePersonAt[] = [];
  for (const p of people) {
    const covering = p.segments.filter((s) => covers(s, t));
    if (!covering.length) continue;
    const seg =
      covering.find((s) => s.kind === "actual") ?? covering[0]!;
    out.push({
      employee: p.employee,
      labor_bucket: p.labor_bucket,
      kind: seg.kind,
      startMin: seg.startMin,
      endMin: seg.endMin,
      hours: seg.hours,
    });
  }
  return out.sort((a, b) => a.startMin - b.startMin || a.employee.localeCompare(b.employee));
}

/** Snap a continuous minute to the occupancy series step. */
export function snapMinute(t: number, axisStart: number, stepMin = 15): number {
  const rel = Math.max(0, t - axisStart);
  return axisStart + Math.floor(rel / stepMin) * stepMin;
}
