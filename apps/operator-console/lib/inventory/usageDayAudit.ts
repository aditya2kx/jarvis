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

/** Calm tag for the matrix only — never "force in/out" (that lives in the drawer). */
export function matrixStatusTag(row: UsageDayAuditRow): string {
  if (row.status === "included") return "in avg";
  const reason = (row.reason ?? "excluded").trim();
  if (
    reason === "included" ||
    reason === "force_include" ||
    reason === "force_exclude" ||
    reason === "operator force_exclude" ||
    reason.startsWith("force_")
  ) {
    return "excluded";
  }
  return reason.length > 18 ? `${reason.slice(0, 16)}…` : reason;
}

/** Drawer / detail label — may mention sticky override. */
export function statusTag(row: UsageDayAuditRow): string {
  if (row.override_mode === "force_include") return "force include";
  if (row.override_mode === "force_exclude") return "force exclude";
  return matrixStatusTag(row);
}

export function matrixChipVariant(
  row: UsageDayAuditRow,
): "secondary" | "outline" {
  return row.status === "included" ? "secondary" : "outline";
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

/** Consumption units for the outlier pool (stock drop); restock Δ → 0. */
export function usageUnitsFromDelta(delta: number | null | undefined): number | null {
  if (delta == null || Number.isNaN(Number(delta))) return null;
  return Math.max(-Number(delta), 0);
}

function median(nums: number[]): number | null {
  if (!nums.length) return null;
  const s = [...nums].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 === 1 ? s[mid]! : (s[mid - 1]! + s[mid]!) / 2;
}

function madAbout(nums: number[], med: number): number {
  return median(nums.map((n) => Math.abs(n - med))) ?? 0;
}

/** Low = 0.20×median(nonzero); high = median(survivors)+2.5×1.4826×MAD — mirrors OA SQL. */
export function computeLowHighBars(poolUsages: number[]): {
  low: number | null;
  high: number | null;
  medianNonzero: number | null;
} {
  const nonzero = poolUsages.filter((u) => u > 0);
  const medianNonzero = median(nonzero);
  const low = medianNonzero == null ? null : 0.2 * medianNonzero;
  const survivors = poolUsages.filter((u) => {
    if (u <= 0) return false;
    if (low != null && u < low) return false;
    return true;
  });
  const forHigh = survivors.length ? survivors : nonzero;
  const medSurv = median(forHigh);
  if (medSurv == null) return { low, high: null, medianNonzero };
  const mad = madAbout(forHigh, medSurv);
  const high = mad > 0 ? medSurv + 2.5 * 1.4826 * mad : null;
  return { low, high, medianNonzero };
}

export type OverrideDraftChoice = "rule" | "force_include" | "force_exclude";

export type ThresholdImpact = {
  usage: number | null;
  lowBefore: number | null;
  highBefore: number | null;
  lowAfter: number | null;
  highAfter: number | null;
  passesHighBefore: boolean | null;
  passesHighAfter: boolean | null;
  inPoolBefore: boolean;
  inPoolAfter: boolean;
};

/**
 * Approximate low/high bars before vs after a draft override for one (item, date).
 * Pool ≈ days currently in the avg (`in_avg` / included); draft force_include adds
 * this day’s usage, force_exclude removes it. Unique-weekday / gap rules stay server-side.
 */
export function thresholdImpactForDraft(
  itemRows: UsageDayAuditRow[],
  draftDate: string,
  draft: OverrideDraftChoice,
): ThresholdImpact {
  const focus = itemRows.find((r) => r.submitted_date === draftDate);
  const usage = usageUnitsFromDelta(focus?.delta ?? null);

  const beforeUsages: number[] = [];
  for (const r of itemRows) {
    const u = usageUnitsFromDelta(r.delta);
    if (u == null) continue;
    const inPool = r.in_avg === true || r.status === "included";
    if (inPool) beforeUsages.push(u);
  }

  const afterUsages = [...beforeUsages];
  const inPoolBefore =
    focus != null && (focus.in_avg === true || focus.status === "included");
  let inPoolAfter = inPoolBefore;
  if (draft === "force_include" && usage != null) {
    if (!inPoolBefore) {
      afterUsages.push(usage);
      inPoolAfter = true;
    }
  } else if (draft === "force_exclude" && inPoolBefore && usage != null) {
    const idx = afterUsages.indexOf(usage);
    if (idx >= 0) afterUsages.splice(idx, 1);
    inPoolAfter = false;
  } else if (draft === "rule" && focus) {
    // Revert sticky override toward rule: if currently force_include but rule would exclude
    if (focus.override_mode === "force_include" && focus.rule_eligible === false && inPoolBefore) {
      const idx = afterUsages.indexOf(usage ?? -1);
      if (idx >= 0 && usage != null) afterUsages.splice(idx, 1);
      inPoolAfter = false;
    } else if (focus.override_mode === "force_exclude" && focus.rule_eligible === true && usage != null) {
      if (!inPoolBefore) {
        afterUsages.push(usage);
        inPoolAfter = true;
      }
    }
  }

  const before = computeLowHighBars(beforeUsages);
  const after = computeLowHighBars(afterUsages);

  // Prefer live BQ high_bar when draft matches current (more accurate than client pool).
  const highBefore =
    focus?.high_bar != null && seedMatchesCurrent(focus, draft)
      ? focus.high_bar
      : before.high;
  const highAfter = after.high;
  const lowBefore = before.low;
  const lowAfter = after.low;

  const passes = (high: number | null): boolean | null => {
    if (usage == null) return null;
    if (high == null) return true;
    return usage <= high;
  };

  return {
    usage,
    lowBefore,
    highBefore,
    lowAfter,
    highAfter,
    passesHighBefore: passes(highBefore),
    passesHighAfter: passes(highAfter),
    inPoolBefore,
    inPoolAfter,
  };
}

function seedMatchesCurrent(row: UsageDayAuditRow, draft: OverrideDraftChoice): boolean {
  if (draft === "force_include") return row.override_mode === "force_include";
  if (draft === "force_exclude") return row.override_mode === "force_exclude";
  return !row.override_mode;
}

function fmtTubs(v: number | null): string {
  if (v == null) return "—";
  const n = Number(v);
  // One decimal is enough for operators; drop trailing .0.
  return Number.isInteger(n) ? String(n) : n.toFixed(1).replace(/\.0$/, "");
}

function fmtRange(low: number | null, high: number | null): string {
  if (low == null && high == null) return "unknown";
  if (high == null) return low == null ? "unknown" : `${fmtTubs(low)}+ tubs`;
  if (low == null) return `up to ${fmtTubs(high)} tubs`;
  return `${fmtTubs(low)}–${fmtTubs(high)} tubs`;
}

/** Plain-language preview for operators (no median/MAD jargon). */
export function formatThresholdImpact(impact: ThresholdImpact): string {
  const used =
    impact.usage == null ? "Usage unknown" : `Used ~${fmtTubs(impact.usage)} tubs that day`;

  const avg =
    impact.inPoolBefore === impact.inPoolAfter
      ? impact.inPoolAfter
        ? "Already in the average"
        : "Not in the average"
      : impact.inPoolAfter
        ? "Will add to the average"
        : "Will remove from the average";

  // When avg membership doesn't change, show the live "before" band only —
  // client after-high can be null (MAD=0) while BQ high_bar is set, which
  // looked like a scary "3.11 → —" with no real change.
  let range: string;
  if (impact.inPoolBefore === impact.inPoolAfter) {
    range = `Typical day: ${fmtRange(impact.lowBefore, impact.highBefore)}`;
  } else {
    const before = fmtRange(impact.lowBefore, impact.highBefore);
    const after = fmtRange(impact.lowAfter, impact.highAfter);
    range =
      before === after
        ? `Typical day: ${before}`
        : `Typical day: ${before} → ${after}`;
  }

  let tomorrow: string;
  if (impact.passesHighBefore == null || impact.passesHighAfter == null) {
    tomorrow = "Similar day tomorrow: unclear";
  } else if (impact.passesHighBefore === impact.passesHighAfter) {
    tomorrow = impact.passesHighAfter
      ? "Similar day tomorrow: still counts on its own"
      : "Similar day tomorrow: still needs an override";
  } else if (impact.passesHighAfter) {
    tomorrow = "Similar day tomorrow: would start counting on its own";
  } else {
    tomorrow = "Similar day tomorrow: would need an override";
  }

  return [used + " · " + avg, range, tomorrow].join("\n");
}
