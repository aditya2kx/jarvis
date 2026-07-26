import type { UsageDayAuditRow } from "@/lib/bq/queries";

export type UsageDayGroup = {
  date: string;
  included: UsageDayAuditRow[];
  excluded: UsageDayAuditRow[];
};

/** @deprecated Prefer pivotUsageDayAudit (date × base matrix). Kept for tests. */
export function groupUsageDayAudit(rows: UsageDayAuditRow[]): UsageDayGroup[] {
  const byDate = new Map<string, UsageDayAuditRow[]>();
  for (const r of rows) {
    const d = r.submitted_date;
    if (!byDate.has(d)) byDate.set(d, []);
    byDate.get(d)!.push(r);
  }
  const dates = [...byDate.keys()].sort((a, b) => (a < b ? 1 : a > b ? -1 : 0));
  return dates.map((date) => {
    const list = byDate.get(date)!;
    return {
      date,
      included: list.filter((r) => r.status === "included"),
      excluded: list.filter((r) => r.status !== "included"),
    };
  });
}

export type UsageDayMatrix = {
  /** Newest first. */
  dates: string[];
  /** Stable column order (localeCompare). */
  bases: string[];
  /** Key `${date}\0${item}` → row. */
  cells: Map<string, UsageDayAuditRow>;
};

export function cellKey(date: string, item: string): string {
  return `${date}\0${item}`;
}

/** Pivot audit rows into a date × base matrix for horizontal scrolling. */
export function pivotUsageDayAudit(rows: UsageDayAuditRow[]): UsageDayMatrix {
  const cells = new Map<string, UsageDayAuditRow>();
  const dateSet = new Set<string>();
  const baseSet = new Set<string>();
  for (const r of rows) {
    dateSet.add(r.submitted_date);
    baseSet.add(r.item);
    cells.set(cellKey(r.submitted_date, r.item), r);
  }
  const dates = [...dateSet].sort((a, b) => (a < b ? 1 : a > b ? -1 : 0));
  const bases = [...baseSet].sort((a, b) => a.localeCompare(b));
  return { dates, bases, cells };
}

export function formatQty(qty: number | null | undefined): string {
  if (qty == null || Number.isNaN(Number(qty))) return "—";
  return Number(qty).toFixed(1);
}

export function formatDelta(delta: number | null | undefined): string {
  if (delta == null || Number.isNaN(Number(delta))) return "—";
  const n = Number(delta);
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(1)}`;
}

/** Short status tag for a matrix cell. */
export function statusTag(row: UsageDayAuditRow): string {
  if (row.override_mode === "force_include") return "force in";
  if (row.override_mode === "force_exclude") return "force out";
  if (row.status === "included") return "in avg";
  const reason = (row.reason ?? "excluded").trim();
  // Drop redundant "excluded" prefix from SQL notes when present.
  if (reason === "included") return "in avg";
  return reason.length > 18 ? `${reason.slice(0, 16)}…` : reason;
}

export function previewLine(opts: {
  highBarBefore: number | null | undefined;
  highBarAfter: number | null | undefined;
  similarPasses: boolean | null | undefined;
  delta: number | null | undefined;
}): string {
  const fmt = (v: number | null | undefined) =>
    v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(2);
  const before = fmt(opts.highBarBefore);
  const after = fmt(opts.highBarAfter);
  const tomorrow =
    opts.similarPasses == null
      ? "unknown"
      : opts.similarPasses
        ? "auto-include"
        : "still excluded";
  return `High bar ${before}→${after} · similar Δ tomorrow: ${tomorrow}`;
}
