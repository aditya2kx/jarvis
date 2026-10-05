// Pure model for the /labor "Punches" table (Issue #356). Every clocked
// person-day in the Period (adp_punches) is a row; days with an issue come from
// adp_timecard_gaps (ADP Timecards month view, which still shows an open entry
// the Timecard XLSX drops) and replace the plain row. Both carry the latest
// punch_gap_decisions row. Kinds mirror skills/bhaga_labor/punch_gaps.py, plus
// `ok` for a day with nothing to fix.

import { parseShiftRangesJson } from "@/lib/labor/shift-ranges";

export type PunchGapKind =
  | "missing_out"
  | "missing_out_after_break"
  | "in_progress"
  | "no_entry"
  | "missing_in"
  | "ok";

export type PunchGapAction = "accept" | "edit" | "reject";

export type PunchGapStatus =
  | "recorded"
  | "pending_write"
  | "applying"
  | "applied"
  /** Saved in ADP, but ADP's Timecard export (→ hours, payroll) lacks it yet. */
  | "not_in_hours"
  | "failed"
  | "already_resolved"
  | "rejected";

/** Already in ADP (or being written): never re-decide or re-write these. */
export function isWrittenToAdp(status: PunchGapStatus | undefined): boolean {
  return status === "applying" || status === "applied" || status === "not_in_hours";
}

export interface PunchEntry {
  in: string | null;
  out: string | null;
  hours: number;
}

export interface PunchGapCoworker {
  employee: string;
  in_time: string | null;
  out_time: string | null;
}

/** Wire shape from lib/bq/queries.ts::laborPunchGaps. */
export interface PunchGapRow {
  date: string;
  employee_id: string;
  kind: PunchGapKind;
  entries_json: string | null;
  scheduled_ranges_json: string | null;
  open_entry_index: number | null;
  suggested_in?: string | null;
  suggested_out: string | null;
  suggested_hours: number | null;
  rule: string | null;
  adp_error_flag: boolean | null;
  decision_id: string | null;
  decision_action: PunchGapAction | null;
  decision_in_time: string | null;
  decision_out_time: string | null;
  decision_status: PunchGapStatus | null;
  decision_error: string | null;
  decided_by: string | null;
  decided_at: string | null;
  [key: string]: unknown;
}

export interface PunchGap {
  key: string;
  date: string;
  employee: string;
  kind: PunchGapKind;
  entries: PunchEntry[];
  scheduled: [string, string][];
  openIndex: number | null;
  /** no_entry only: the scheduled start the new entry would use. */
  suggestedIn: string | null;
  suggestedOut: string | null;
  suggestedHours: number | null;
  rule: string | null;
  adpFlagged: boolean;
  coworkers: PunchGapCoworker[];
  decision: {
    id: string | null;
    action: PunchGapAction;
    inTime: string | null;
    outTime: string | null;
    status: PunchGapStatus;
    error: string | null;
    by: string | null;
    at: string | null;
  } | null;
}

export const KIND_LABEL: Record<PunchGapKind, string> = {
  missing_out: "Missing clock-out",
  missing_out_after_break: "Missing clock-out",
  in_progress: "Still on shift",
  no_entry: "Scheduled, no punch",
  missing_in: "Missing clock-in",
  ok: "No issue",
};

const HHMM = /^([01]\d|2[0-3]):([0-5]\d)$/;

export function gapKey(date: string, employee: string): string {
  return `${date}|${employee}`;
}

export function toMinutes(hhmm: string): number | null {
  const m = HHMM.exec(hhmm ?? "");
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}

/** "20:30" → "8:30 PM" (the way ADP and the team read times). */
export function formatClock(hhmm: string | null): string {
  const min = hhmm ? toMinutes(hhmm) : null;
  if (min == null) return "—";
  const h = Math.floor(min / 60);
  const mm = String(min % 60).padStart(2, "0");
  return `${h % 12 || 12}:${mm} ${h < 12 ? "AM" : "PM"}`;
}

function parseJson<T>(raw: string | null, fallback: T): T {
  if (!raw) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

type DecisionColumns = Pick<
  PunchGapRow,
  | "decision_id"
  | "decision_action"
  | "decision_in_time"
  | "decision_out_time"
  | "decision_status"
  | "decision_error"
  | "decided_by"
  | "decided_at"
>;

type CoworkerShift = { date: string; employee: string; in_time: string | null; out_time: string | null };

function toDecision(r: DecisionColumns): PunchGap["decision"] {
  return r.decision_action && r.decision_status
    ? {
        id: r.decision_id ?? null,
        action: r.decision_action,
        inTime: r.decision_in_time,
        outTime: r.decision_out_time,
        status: r.decision_status,
        error: r.decision_error,
        by: r.decided_by,
        at: r.decided_at,
      }
    : null;
}

function coworkersOn(shifts: CoworkerShift[], date: string, employee: string): PunchGapCoworker[] {
  return shifts
    .filter((s) => s.date.slice(0, 10) === date && s.employee !== employee)
    .map((s) => ({ employee: s.employee, in_time: s.in_time, out_time: s.out_time }));
}

function byDayThenEmployee(a: PunchGap, b: PunchGap): number {
  return b.date.localeCompare(a.date) || a.employee.localeCompare(b.employee);
}

/** Wire shape from lib/bq/queries.ts::laborPunchDays — one clocked person-day. */
export interface PunchDayRow extends DecisionColumns {
  date: string;
  employee: string;
  entries_json: string | null;
  /** ADP Team Schedule strings, e.g. ["1:30 PM - 8:30 PM"]. */
  shift_ranges_json: string | null;
  [key: string]: unknown;
}

function hhmm(min: number): string {
  const m = ((min % 1440) + 1440) % 1440;
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

/**
 * Every clocked day as an `ok` row, then replaced by its gap row when ADP
 * flags the same person-day — one row per person per day.
 */
export function mergePunchDays(
  gaps: PunchGap[],
  days: PunchDayRow[],
  coworkerShifts: CoworkerShift[],
): PunchGap[] {
  const flagged = new Set(gaps.map((g) => g.key));
  const plain = days
    .map((r): PunchGap => {
      const date = r.date.slice(0, 10);
      return {
        key: gapKey(date, r.employee),
        date,
        employee: r.employee,
        kind: "ok",
        entries: parseJson<PunchEntry[]>(r.entries_json, []),
        scheduled: parseShiftRangesJson(r.shift_ranges_json).map(
          (x) => [hhmm(x.startMin), hhmm(x.endMin)] as [string, string],
        ),
        openIndex: null,
        suggestedIn: null,
        suggestedOut: null,
        suggestedHours: null,
        rule: null,
        adpFlagged: false,
        coworkers: coworkersOn(coworkerShifts, date, r.employee),
        decision: toDecision(r),
      };
    })
    .filter((g) => !flagged.has(g.key));
  return [...gaps, ...plain].sort(byDayThenEmployee);
}

export function buildPunchGaps(
  rows: PunchGapRow[],
  coworkerShifts: CoworkerShift[],
): PunchGap[] {
  return rows
    .map((r) => {
      const date = r.date.slice(0, 10);
      return {
        key: gapKey(date, r.employee_id),
        date,
        employee: r.employee_id,
        kind: r.kind,
        entries: parseJson<PunchEntry[]>(r.entries_json, []),
        scheduled: parseJson<[string, string][]>(r.scheduled_ranges_json, []),
        openIndex: r.open_entry_index,
        suggestedIn: r.suggested_in ?? null,
        suggestedOut: r.suggested_out,
        suggestedHours: r.suggested_hours != null ? Number(r.suggested_hours) : null,
        rule: r.rule,
        adpFlagged: Boolean(r.adp_error_flag),
        coworkers: coworkersOn(coworkerShifts, date, r.employee_id),
        decision: toDecision(r),
      } satisfies PunchGap;
    })
    .sort(byDayThenEmployee);
}

/** The clock-in being closed (open entry), or null for no_entry / missing_in. */
export function openClockIn(gap: Pick<PunchGap, "entries" | "openIndex">): string | null {
  if (gap.openIndex == null) return null;
  return gap.entries[gap.openIndex]?.in ?? null;
}

/** Hours an Out time would add to the open entry (2dp), or null if invalid. */
export function hoursIfClosedAt(clockIn: string | null, out: string | null): number | null {
  const a = clockIn ? toMinutes(clockIn) : null;
  const b = out ? toMinutes(out) : null;
  if (a == null || b == null || b <= a) return null;
  return Math.round(((b - a) / 60) * 100) / 100;
}

export type DecisionInput = {
  date: string;
  employee: string;
  action: PunchGapAction;
  inTime?: string | null;
  outTime?: string | null;
  note?: string | null;
};

/**
 * Validate a decision against the gap it targets; returns an error string or
 * null. The same check runs client-side (inline hint) and in the server action
 * (authoritative), so an edit can never write an Out before its In.
 */
export function validateDecision(
  gap: Pick<PunchGap, "kind" | "entries" | "openIndex" | "suggestedIn" | "suggestedOut">,
  input: Pick<DecisionInput, "action" | "inTime" | "outTime">,
): string | null {
  if (input.action === "reject") return null;
  if (gap.kind === "in_progress") return "Still on shift — wait until the day is over.";
  if (gap.kind === "missing_in") {
    return "Missing clock-in rows are review-only for now — fix in ADP.";
  }
  if (input.action === "accept") {
    if (!gap.suggestedOut || (gap.kind === "no_entry" && !gap.suggestedIn)) {
      return "No suggestion to accept — edit instead.";
    }
    return null;
  }
  if (gap.kind === "no_entry") {
    const a = input.inTime ? toMinutes(input.inTime) : null;
    const b = input.outTime ? toMinutes(input.outTime) : null;
    if (a == null || b == null) return "Enter both In and Out times.";
    return b > a ? null : "Out must be after In.";
  }
  if (!input.outTime || toMinutes(input.outTime) == null) return "Enter an Out time.";
  return hoursIfClosedAt(openClockIn(gap), input.outTime) == null
    ? `Out must be after the ${formatClock(openClockIn(gap))} clock-in.`
    : null;
}

export interface PunchGapSummary {
  total: number;
  undecided: number;
  suggestedHours: number;
}

/**
 * Still needs the operator: undecided, or accepted/edited but not yet in ADP
 * (recorded, writing, or a failed write). Leaves once ADP has the punch or the
 * row is dismissed — so an accepted row stays visible and revertible until then.
 */
export function isOpenGap(gap: PunchGap): boolean {
  if (gap.kind === "ok") return false;
  const d = gap.decision;
  if (!d) return true;
  if (d.action === "reject") return false;
  return d.status !== "applied" && d.status !== "already_resolved";
}

export function summarizePunchGaps(gaps: PunchGap[]): PunchGapSummary {
  const actionable = gaps.filter((g) => g.kind !== "in_progress" && g.kind !== "ok");
  return {
    total: actionable.length,
    undecided: actionable.filter((g) => !g.decision).length,
    suggestedHours:
      Math.round(
        actionable
          .filter((g) => !g.decision)
          .reduce((s, g) => s + (g.suggestedHours ?? 0), 0) * 100,
      ) / 100,
  };
}

/** Filterable / sortable columns of the Punches table. */
export type PunchGapColumn = "day" | "employee" | "issue" | "suggestion" | "decision";
export type PunchGapSort = { column: PunchGapColumn; desc: boolean };
export type PunchGapFilters = Partial<Record<Exclude<PunchGapColumn, "suggestion">, string[]>>;

export function decisionLabel(gap: PunchGap): string {
  const d = gap.decision;
  if (!d) return gap.kind === "ok" ? "Nothing to decide" : "Needs decision";
  if (d.action === "reject") return "Dismissed";
  if (d.status === "applied") return "Written to ADP";
  if (d.status === "not_in_hours") return "In ADP, not in hours yet";
  if (d.status === "already_resolved") return "Already fixed in ADP";
  return d.action === "accept" ? "Accepted" : "Edited";
}

/** "9:00 AM – 3:00 PM" for a new entry, "out 3:00 PM" for a clock-out. */
export function decisionTimes(d: { inTime: string | null; outTime: string | null }): string {
  return d.inTime
    ? `${formatClock(d.inTime)} – ${formatClock(d.outTime)}`
    : `out ${formatClock(d.outTime)}`;
}

/** Filter value per column; `day` uses the ISO date (labels are presentation). */
function facetValue(gap: PunchGap, column: keyof PunchGapFilters): string {
  if (column === "day") return gap.date;
  if (column === "employee") return gap.employee;
  if (column === "issue") return KIND_LABEL[gap.kind];
  return decisionLabel(gap);
}

/** Rows passing every column filter except `skip` (so each filter's options stay faceted). */
export function filterPunchGaps(
  gaps: PunchGap[],
  filters: PunchGapFilters,
  skip?: keyof PunchGapFilters,
): PunchGap[] {
  const active = (Object.entries(filters) as [keyof PunchGapFilters, string[]][]).filter(
    ([c, v]) => c !== skip && v.length > 0,
  );
  return gaps.filter((g) => active.every(([c, v]) => v.includes(facetValue(g, c))));
}

/** Distinct values for a column among rows matching the other filters (days chronological). */
export function punchGapOptions(
  gaps: PunchGap[],
  filters: PunchGapFilters,
  column: keyof PunchGapFilters,
): string[] {
  const values = new Set(filterPunchGaps(gaps, filters, column).map((g) => facetValue(g, column)));
  for (const v of filters[column] ?? []) values.add(v);
  return [...values].sort();
}

/** Stable sort by one column; ties fall back to day, then employee. */
export function sortPunchGaps(gaps: PunchGap[], sort: PunchGapSort): PunchGap[] {
  const key = (g: PunchGap): string | number =>
    sort.column === "suggestion"
      ? (g.suggestedHours ?? -1)
      : facetValue(g, sort.column);
  const dir = sort.desc ? -1 : 1;
  return [...gaps].sort((a, b) => {
    const ka = key(a);
    const kb = key(b);
    const primary =
      typeof ka === "number" && typeof kb === "number"
        ? ka - kb
        : String(ka).localeCompare(String(kb));
    return primary * dir || a.date.localeCompare(b.date) || a.employee.localeCompare(b.employee);
  });
}

/** Timeline axis (minutes) covering every punch, schedule, coworker and suggestion, padded to the hour. */
export function timelineBounds(gaps: PunchGap[]): { start: number; end: number } {
  const mins: number[] = [];
  for (const g of gaps) {
    for (const e of g.entries) {
      for (const t of [e.in, e.out]) if (t) mins.push(toMinutes(t) ?? NaN);
    }
    for (const [a, b] of g.scheduled) mins.push(toMinutes(a) ?? NaN, toMinutes(b) ?? NaN);
    for (const t of [g.suggestedIn, g.suggestedOut]) if (t) mins.push(toMinutes(t) ?? NaN);
  }
  const ok = mins.filter((m) => Number.isFinite(m));
  if (!ok.length) return { start: 6 * 60, end: 22 * 60 };
  return {
    start: Math.floor(Math.min(...ok) / 60) * 60,
    end: Math.ceil(Math.max(...ok) / 60) * 60,
  };
}

export function pct(min: number, bounds: { start: number; end: number }): number {
  const span = bounds.end - bounds.start || 1;
  return Math.min(100, Math.max(0, ((min - bounds.start) / span) * 100));
}

/**
 * Accepted/edited punches not yet in ADP (or whose write failed) — what the
 * "Write to ADP" button sends: a clock-out for an open entry, or a whole new
 * entry for a scheduled day with no punch.
 */
export function writableToAdp(gaps: PunchGap[]): PunchGap[] {
  return gaps.filter(
    (g) =>
      (g.kind === "missing_out" || g.kind === "missing_out_after_break" || g.kind === "no_entry") &&
      g.decision != null &&
      g.decision.id != null &&
      g.decision.action !== "reject" &&
      (g.decision.status === "recorded" || g.decision.status === "failed"),
  );
}
