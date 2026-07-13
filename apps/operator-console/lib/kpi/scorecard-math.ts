import type { BaseRunwayRow, OrderQualityDailyRow } from "@/lib/bq/queries";
import type { GoalStatus } from "@/lib/kpi/health-types";

export type { GoalStatus };

// Presentation-only comparison against a store_config goal.
// When goal is 0 and lowerIsBetter (e.g. bases at risk max=0): actual 0 →
// pace 1 (on-track); any positive actual → pace 0 (off-track).
export function paceFor(
  actual: number | null,
  goal: number | null,
  lowerIsBetter: boolean,
): number | null {
  if (actual == null || goal == null) return null;
  if (goal === 0) {
    if (!lowerIsBetter) return null;
    return actual === 0 ? 1 : 0;
  }
  return lowerIsBetter ? goal / actual : actual / goal;
}

export function statusFor(pace: number | null): GoalStatus {
  if (pace == null) return "no-goal";
  if (pace >= 1) return "on-track";
  if (pace >= 0.85) return "at-risk";
  return "off-track";
}

/** Worst-wins rollup for the Home hero health badge. */
export function rollupStatus(statuses: GoalStatus[]): GoalStatus {
  const rank: Record<GoalStatus, number> = {
    "off-track": 0,
    "at-risk": 1,
    "no-goal": 2,
    "on-track": 3,
  };
  let worst: GoalStatus = "on-track";
  for (const s of statuses) {
    if (rank[s] < rank[worst]) worst = s;
  }
  if (statuses.length && statuses.every((s) => s === "no-goal")) return "no-goal";
  return worst;
}

/** Count of Risky rows in the Base runway view (Issue #158). */
export function countRiskyBases(rows: BaseRunwayRow[]): number {
  return rows.filter((r) => r.Status === "Risky").length;
}

/** Mean KDS per-item p95 minutes over the window (Issue #158). */
export function avgPrepP95Min(rows: OrderQualityDailyRow[]): number | null {
  const vals = rows
    .map((r) => (r.kds_p95_min != null ? Number(r.kds_p95_min) : null))
    .filter((v): v is number => v != null);
  if (!vals.length) return null;
  return vals.reduce((s, v) => s + v, 0) / vals.length;
}

/**
 * Inclusive day count for averages — caps `end` at `todayIso` so open
 * periods (this_month / this_week) do not dilute by future calendar days.
 */
export function elapsedDaysInWindow(start: string, end: string, todayIso: string): number {
  const cappedEnd = end < todayIso ? end : todayIso;
  const s = Date.parse(`${start}T12:00:00Z`);
  const e = Date.parse(`${cappedEnd}T12:00:00Z`);
  if (!Number.isFinite(s) || !Number.isFinite(e) || e < s) return 1;
  return Math.max(1, Math.round((e - s) / 86_400_000) + 1);
}
