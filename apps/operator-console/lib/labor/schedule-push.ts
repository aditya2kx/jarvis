/** Draft shifts pushed to ADP (Issue #337) — shared by the store, actions and UI. */

export type PushShift = {
  date: string;
  /** null = open (unassigned) shift. */
  employee: string | null;
  startMin: number;
  endMin: number;
};

/** deleted = removed from ADP (the operator replaced it); skipped = an earlier save already drafted it. */
export type PushStatus = "queued" | "drafted" | "skipped" | "failed" | "published" | "deleted";

export type PushRow = {
  push_id: string;
  row_key: string;
  date: string;
  employee: string | null;
  start_min: number;
  end_min: number;
  status: PushStatus;
  error: string | null;
  updated_at: string | null;
};

export type PushSummary = Record<PushStatus, number> & { errors: string[] };

const ISO = /^\d{4}-\d{2}-\d{2}$/;
const MAX_SHIFTS = 80;

export function shiftKey(s: PushShift): string {
  return [s.date, s.employee ?? "OPEN", s.startMin, s.endMin].join("|");
}

/** Matches adp_schedule_write's duplicate check: one ADP shift per key. */
export function pushRowKey(store: string, s: PushShift): string {
  return `${store}|${shiftKey(s)}`;
}

function rowShift(r: PushRow): PushShift {
  return { date: r.date, employee: r.employee, startMin: Number(r.start_min), endMin: Number(r.end_min) };
}

/**
 * The plan the operator saved: every shift sent to ADP for these rows (latest row
 * per shift), whether it landed or still needs a retry. Once a week has one, the
 * console shows this instead of re-drafting, so reloads and rule edits can't change it.
 */
export function savedPlan(rows: PushRow[]): (PushShift & { status: PushStatus })[] {
  return rows
    .filter((r) => r.status !== "skipped" && r.status !== "deleted")
    .map((r) => ({ ...rowShift(r), status: r.status }))
    .sort((a, b) => a.date.localeCompare(b.date) || a.startMin - b.startMin);
}

type ScheduleLane = { employee: string; open: boolean; segments: { startMin: number; endMin: number }[] };

/**
 * True once ADP's synced schedule carries this exact shift (same person — or an
 * open shift — and the same start/end). Until the next ADP sync a saved draft
 * isn't there yet, so the console keeps showing it as suggested.
 */
export function inAdpSchedule(s: PushShift, lanes: readonly ScheduleLane[]): boolean {
  const want = s.employee?.replace(/\s+/g, " ").trim().toLowerCase();
  return lanes.some((l) => {
    if (want == null ? !l.open : l.open) return false;
    if (want != null) {
      const name = l.employee.replace(/\s+/g, " ").trim().toLowerCase();
      if (name !== want && !name.startsWith(`${want} `)) return false;
    }
    return l.segments.some((g) => g.startMin === s.startMin && g.endMin === s.endMin);
  });
}

/**
 * Rows worth showing for the current draft: anything in (or headed for) ADP, plus
 * failures for shifts still in the draft. A failed row for a shift the draft no
 * longer has never reached ADP and is just history.
 */
export function relevantRows(shifts: PushShift[], rows: PushRow[]): PushRow[] {
  const current = new Set(shifts.map(shiftKey));
  return rows.filter((r) => r.status !== "failed" || current.has(shiftKey(rowShift(r))));
}

/**
 * Draft shifts not yet in ADP (never saved, or the save failed) vs already there
 * or in flight. 'skipped' means an earlier save already drafted it.
 */
export function splitAgainstAdp(
  shifts: PushShift[],
  rows: PushRow[],
): { toSave: PushShift[]; inAdp: PushShift[] } {
  const held = new Set(rows.filter((r) => r.status !== "failed").map((r) => shiftKey(rowShift(r))));
  const toSave: PushShift[] = [];
  const inAdp: PushShift[] = [];
  for (const s of shifts) (held.has(shiftKey(s)) ? inAdp : toSave).push(s);
  return { toSave, inAdp };
}

function addDays(iso: string, n: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  const t = new Date(Date.UTC(y!, m! - 1, d! + n));
  return t.toISOString().slice(0, 10);
}

/** Throws a message fit for a toast; returns the shifts that will be queued. */
export function validatePushShifts(weekStart: string, shifts: unknown, todayIso: string): PushShift[] {
  if (!ISO.test(weekStart) || new Date(`${weekStart}T12:00:00Z`).getUTCDay() !== 1) {
    throw new Error("Week start must be a Monday (YYYY-MM-DD).");
  }
  if (!Array.isArray(shifts) || shifts.length === 0) throw new Error("No draft shifts to save.");
  if (shifts.length > MAX_SHIFTS) throw new Error(`Too many shifts (${shifts.length}); max ${MAX_SHIFTS}.`);
  const weekEnd = addDays(weekStart, 6);
  return shifts.map((raw, i) => {
    const s = raw as Partial<PushShift>;
    const where = `Shift ${i + 1}`;
    if (typeof s.date !== "string" || !ISO.test(s.date)) throw new Error(`${where}: bad date.`);
    if (s.date < weekStart || s.date > weekEnd) throw new Error(`${where}: ${s.date} is outside the week.`);
    if (s.date <= todayIso) throw new Error(`${where}: ${s.date} is today or in the past.`);
    const { startMin, endMin } = s;
    if (
      !Number.isInteger(startMin) ||
      !Number.isInteger(endMin) ||
      startMin! < 0 ||
      endMin! > 24 * 60 ||
      endMin! <= startMin!
    ) {
      throw new Error(`${where}: bad start/end time.`);
    }
    const employee = s.employee == null ? null : String(s.employee).trim() || null;
    return { date: s.date, employee, startMin: startMin!, endMin: endMin! };
  });
}

export function summarizePush(rows: PushRow[]): PushSummary {
  const out: PushSummary = { queued: 0, drafted: 0, skipped: 0, failed: 0, published: 0, deleted: 0, errors: [] };
  for (const r of rows) {
    out[r.status] += 1;
    if (r.error) out.errors.push(r.error);
  }
  return out;
}
