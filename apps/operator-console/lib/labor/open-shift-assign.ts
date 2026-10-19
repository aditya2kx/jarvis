/** ADP open shifts the operator assigns from the console (Issue #412) — shared by the store, actions and UI. */

export type AssignItem = {
  date: string;
  startMin: number;
  endMin: number;
  employee: string;
};

export type AssignStatus = "queued" | "assigned" | "skipped" | "failed";

export type AssignRow = {
  assign_id: string;
  row_key: string;
  date: string;
  employee: string;
  start_min: number;
  end_min: number;
  status: AssignStatus;
  error: string | null;
  adp_name: string | null;
  updated_at: string | null;
};

const ISO = /^\d{4}-\d{2}-\d{2}$/;
const MAX_ITEMS = 40;

/** Matches adp_schedule_write.run_assign's duplicate check. */
export function assignRowKey(store: string, i: AssignItem): string {
  return [store, i.date, i.startMin, i.endMin, i.employee].join("|");
}

/** Throws a message fit for a toast; returns the items that will be queued. */
export function validateAssignItems(items: unknown, todayIso: string): AssignItem[] {
  if (!Array.isArray(items) || items.length === 0) throw new Error("No open shifts selected.");
  if (items.length > MAX_ITEMS) throw new Error(`Too many open shifts (${items.length}); max ${MAX_ITEMS}.`);
  const seen = new Set<string>();
  return items.map((raw, i) => {
    const s = raw as Partial<AssignItem>;
    const where = `Shift ${i + 1}`;
    if (typeof s.date !== "string" || !ISO.test(s.date)) throw new Error(`${where}: bad date.`);
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
    const employee = typeof s.employee === "string" ? s.employee.trim() : "";
    if (!employee) throw new Error(`${where}: choose who takes it.`);
    const key = `${s.date}|${employee}`;
    if (seen.has(key)) throw new Error(`${where}: ${employee} already takes another selected shift on ${s.date}.`);
    seen.add(key);
    return { date: s.date, startMin: startMin!, endMin: endMin!, employee };
  });
}

export type AssignSummary = Record<AssignStatus, number> & { errors: string[] };

export function summarizeAssign(rows: AssignRow[]): AssignSummary {
  const out: AssignSummary = { queued: 0, assigned: 0, skipped: 0, failed: 0, errors: [] };
  for (const r of rows) {
    out[r.status] += 1;
    if (r.status === "failed" && r.error) out.errors.push(`${r.employee} ${r.date}: ${r.error}`);
  }
  return out;
}
