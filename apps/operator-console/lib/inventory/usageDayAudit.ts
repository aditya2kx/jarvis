import type { UsageDayAuditRow } from "@/lib/bq/queries";

export type UsageDayGroup = {
  date: string;
  included: UsageDayAuditRow[];
  excluded: UsageDayAuditRow[];
};

/** Group audit rows by date for layout A (Included / Excluded columns). */
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

export function formatDelta(delta: number | null | undefined): string {
  if (delta == null || Number.isNaN(Number(delta))) return "—";
  const n = Number(delta);
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(1)}`;
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
