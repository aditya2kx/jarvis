/**
 * ADP unavailability → per-day blocked windows (Issue #337). Pending requests
 * count as much as approved ones: ADP only paints a block after approval, but a
 * pending request is still what the employee told us.
 */

import { isoWeekdayMon0 } from "@/lib/labor/staffing-need";
import { timeToMin } from "@/lib/labor/schedule-inputs";
import { parseShiftRangesJson } from "@/lib/labor/shift-ranges";

export type UnavailabilityInput = {
  employee: string;
  status: "pending" | "approved";
  first_date: string;
  from_time: string | null;
  to_time: string | null;
  all_day: boolean;
  repeat_weekday: number | null;
  repeat_until: string | null;
};

export type Block = { fromMin: number; toMin: number; status: "pending" | "approved" };

const DAY: [number, number] = [0, 24 * 60];

/** Does this row cover `iso` (one-off date, or a weekly repeat through repeat_until)? */
export function coversDate(row: UnavailabilityInput, iso: string): boolean {
  const day = iso.slice(0, 10);
  if (day === row.first_date) return true;
  return (
    row.repeat_weekday != null &&
    day > row.first_date &&
    (!row.repeat_until || day <= row.repeat_until) &&
    isoWeekdayMon0(day) === row.repeat_weekday
  );
}

function window(row: UnavailabilityInput): [number, number] {
  if (row.all_day) return DAY;
  const s = timeToMin(row.from_time ?? "");
  const e = timeToMin(row.to_time ?? "");
  if (s == null || e == null) return DAY;
  return [s, e > s ? e : 24 * 60];
}

/** Blocked windows per employee on `iso`. */
export function blocksOn(rows: UnavailabilityInput[], iso: string): Map<string, Block[]> {
  const out = new Map<string, Block[]>();
  for (const r of rows) {
    if (!coversDate(r, iso)) continue;
    const [fromMin, toMin] = window(r);
    out.set(r.employee, [...(out.get(r.employee) ?? []), { fromMin, toMin, status: r.status }]);
  }
  return out;
}

/** Longest part of [s, e) that avoids every block. */
export function freeSegment(s: number, e: number, blocks: readonly Block[] = []): [number, number] {
  let segs: [number, number][] = [[s, e]];
  for (const b of blocks) {
    segs = segs.flatMap(([a, z]): [number, number][] =>
      b.toMin <= a || b.fromMin >= z ? [[a, z]] : [
        ...(b.fromMin > a ? [[a, b.fromMin] as [number, number]] : []),
        ...(b.toMin < z ? [[b.toMin, z] as [number, number]] : []),
      ],
    );
  }
  return segs.reduce<[number, number]>((best, x) => (x[1] - x[0] > best[1] - best[0] ? x : best), [s, s]);
}

export type ScheduledShift = { date: string; employee: string; shift_ranges_json: string | null };

export type Conflict = {
  date: string;
  employee: string;
  shift: { startMin: number; endMin: number };
  block: Block;
};

/** Scheduled ADP shifts that overlap the same person's unavailability. */
export function scheduleConflicts(rows: UnavailabilityInput[], shifts: ScheduledShift[]): Conflict[] {
  const out: Conflict[] = [];
  const byDate = new Map<string, Map<string, Block[]>>();
  for (const s of shifts) {
    const blocks = (byDate.get(s.date) ?? byDate.set(s.date, blocksOn(rows, s.date)).get(s.date)!).get(s.employee);
    if (!blocks) continue;
    for (const r of parseShiftRangesJson(s.shift_ranges_json)) {
      const hit = blocks.find((b) => overlapMinutes(r.startMin, r.endMin, [b]) > 0);
      if (hit) out.push({ date: s.date, employee: s.employee, shift: { startMin: r.startMin, endMin: r.endMin }, block: hit });
    }
  }
  return out.sort((a, b) => a.date.localeCompare(b.date) || a.employee.localeCompare(b.employee));
}

/**
 * ADP paints an approved weekly entry as one block per date. Fold same-person,
 * same-window blocks on the same weekday, a week apart, back into one weekly row.
 */
export function collapseWeekly<T extends UnavailabilityInput>(rows: T[]): T[] {
  const out: T[] = [];
  const groups = new Map<string, T[]>();
  for (const r of rows) {
    if (r.status !== "approved" || r.repeat_weekday != null) {
      out.push(r);
      continue;
    }
    const key = [r.employee, isoWeekdayMon0(r.first_date), r.all_day, r.from_time, r.to_time].join("|");
    groups.set(key, [...(groups.get(key) ?? []), r]);
  }
  for (const g of groups.values()) {
    g.sort((a, b) => a.first_date.localeCompare(b.first_date));
    let run: T[] = [];
    const flush = () => {
      if (run.length > 1) {
        out.push({
          ...run[0]!,
          repeat_weekday: isoWeekdayMon0(run[0]!.first_date),
          repeat_until: run[run.length - 1]!.first_date,
        });
      } else out.push(...run);
      run = [];
    };
    for (const r of g) {
      const prev = run[run.length - 1];
      if (prev && daysBetween(prev.first_date, r.first_date) !== 7) flush();
      run.push(r);
    }
    flush();
  }
  return out;
}

export type PatternLine<T> = {
  /** Mon=0 weekdays sharing these hours and end date; empty for a one-off date. */
  weekdays: number[];
  /** One-off date, or the first remaining date of a weekly pattern. */
  date: string;
  until: string | null;
  all_day: boolean;
  from_time: string | null;
  to_time: string | null;
  rows: T[];
};

/**
 * Approved entries still ahead of `todayIso`, one entry per person: weekly
 * repeats sharing hours, start week and end week fold into one line
 * ("Mon–Fri · All day").
 */
export function approvedByPerson<T extends UnavailabilityInput>(
  rows: T[],
  todayIso: string,
): { employee: string; lines: PatternLine<T>[] }[] {
  const people = new Map<string, Map<string, PatternLine<T>>>();
  for (const r of collapseWeekly(rows.filter((x) => x.status === "approved"))) {
    const end = r.repeat_weekday != null ? (r.repeat_until ?? "9999-12-31") : r.first_date;
    if (end < todayIso) continue;
    const weekly = r.repeat_weekday != null;
    const hours = r.all_day ? "all" : `${r.from_time}-${r.to_time}`;
    const date = weekly ? nextOnOrAfter(r.first_date, todayIso) : r.first_date;
    const lastWeek = r.repeat_until ? weekStart(r.repeat_until) : "";
    const key = weekly ? `w|${hours}|${weekStart(date)}|${lastWeek}` : `d|${hours}|${r.first_date}`;
    const lines = people.get(r.employee) ?? new Map<string, PatternLine<T>>();
    people.set(r.employee, lines);
    const line = lines.get(key);
    if (line) {
      if (weekly && !line.weekdays.includes(r.repeat_weekday!)) line.weekdays.push(r.repeat_weekday!);
      if (date < line.date) line.date = date;
      if (r.repeat_until && line.until && r.repeat_until > line.until) line.until = r.repeat_until;
      line.rows.push(r);
    } else {
      lines.set(key, {
        weekdays: weekly ? [r.repeat_weekday!] : [],
        date,
        until: weekly ? r.repeat_until : null,
        all_day: r.all_day || !r.from_time || !r.to_time,
        from_time: r.from_time,
        to_time: r.to_time,
        rows: [r],
      });
    }
  }
  return [...people.entries()]
    .map(([employee, lines]) => ({
      employee,
      lines: [...lines.values()]
        .map((l) => ({ ...l, weekdays: [...l.weekdays].sort((a, b) => a - b) }))
        .sort(
          (a, b) =>
            (a.weekdays.length ? 0 : 1) - (b.weekdays.length ? 0 : 1) ||
            (a.weekdays[0] ?? 0) - (b.weekdays[0] ?? 0) ||
            a.date.localeCompare(b.date) ||
            (a.from_time ?? "").localeCompare(b.from_time ?? ""),
        ),
    }))
    .sort((a, b) => a.employee.localeCompare(b.employee));
}

function weekStart(iso: string): string {
  return new Date(Date.parse(`${iso}T12:00:00Z`) - isoWeekdayMon0(iso) * 86_400_000).toISOString().slice(0, 10);
}

/** First date on or after `floor` landing on the same weekday as `iso`. */
function nextOnOrAfter(iso: string, floor: string): string {
  if (iso >= floor) return iso;
  const n = Math.ceil(daysBetween(iso, floor) / 7) * 7;
  return new Date(Date.parse(`${iso}T12:00:00Z`) + n * 86_400_000).toISOString().slice(0, 10);
}

function daysBetween(a: string, b: string): number {
  return Math.round((Date.parse(`${b}T12:00:00Z`) - Date.parse(`${a}T12:00:00Z`)) / 86_400_000);
}

/** Minutes of overlap between [s, e) and the blocks. */
export function overlapMinutes(s: number, e: number, blocks: readonly Block[]): number {
  return blocks.reduce((sum, b) => sum + Math.max(0, Math.min(e, b.toMin) - Math.max(s, b.fromMin)), 0);
}
