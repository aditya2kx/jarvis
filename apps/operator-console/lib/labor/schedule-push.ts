/** Weeks saved to ADP as drafts by the retired Save/Publish flow (Issue #337): their plans stay locked. */

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
