/**
 * Forward-looking labor cost math (Issue #166).
 *
 * Completed = punches × wage rates for dates < Chicago today in the window.
 * Projected = completed + remaining scheduled PT hours × avg PT wage
 *             + trailing avg FT $/open-day × forward days,
 *             over completed sales + forecast_orders × trailing AOV.
 *
 * All-in lines multiply wage costs by (1 + labor_burden_pct) when that
 * store_config key is > 0; otherwise all-in fields are null (UI hides them).
 */

export interface LaborForwardRaw {
  completedPtCost: number;
  completedFtCost: number;
  completedNetSales: number;
  completedDayCount: number;
  fwdScheduledHours: number;
  fwdForecastOrders: number;
  fwdDays: number;
  avgPtWage: number | null;
  aov: number | null;
  avgFtCostPerOpenDay: number | null;
  /** Fraction, e.g. 0.13 for 13%. 0 / null → all-in off. */
  laborBurdenPct: number;
  /**
   * When set (>0), use this as forward PT $ instead of
   * `fwdScheduledHours × avgPtWage` (per-employee schedule × wage join).
   */
  fwdPtCostFromEmployees?: number | null;
}

export interface LaborForwardSummary {
  completedPtCost: number | null;
  completedTotalCost: number | null;
  completedNetSales: number | null;
  /** Fraction 0–1 (same unit as store_config labor-% goals). */
  completedPtPct: number | null;
  completedTotalPct: number | null;
  /** Open days with punches in the Period strictly before Chicago today. */
  completedDayCount: number;
  projectedPtCost: number | null;
  projectedTotalCost: number | null;
  projectedNetSales: number | null;
  projectedPtPct: number | null;
  projectedTotalPct: number | null;
  completedPtCostAllIn: number | null;
  completedTotalCostAllIn: number | null;
  completedPtPctAllIn: number | null;
  completedTotalPctAllIn: number | null;
  projectedPtCostAllIn: number | null;
  projectedTotalCostAllIn: number | null;
  projectedPtPctAllIn: number | null;
  projectedTotalPctAllIn: number | null;
  avgPtWage: number | null;
  aov: number | null;
  /** Forward days in Period with ADP scheduled_hours > 0. */
  fwdDays: number;
  fwdScheduledHours: number;
  laborBurdenPct: number;
  hasForward: boolean;
  hasCompleted: boolean;
}

function safeDiv(num: number, den: number): number | null {
  if (!Number.isFinite(num) || !Number.isFinite(den) || den === 0) return null;
  return num / den;
}

function allIn(cost: number | null, burden: number): number | null {
  if (cost == null || !(burden > 0)) return null;
  return cost * (1 + burden);
}

export function computeLaborForwardSummary(raw: LaborForwardRaw): LaborForwardSummary {
  const burden = Number.isFinite(raw.laborBurdenPct) && raw.laborBurdenPct > 0 ? raw.laborBurdenPct : 0;
  const hasCompleted = raw.completedDayCount > 0;
  const hasForward = raw.fwdDays > 0;

  const completedPt = hasCompleted ? raw.completedPtCost : null;
  const completedFt = hasCompleted ? raw.completedFtCost : null;
  const completedTotal =
    completedPt == null && completedFt == null ? null : (completedPt ?? 0) + (completedFt ?? 0);
  const completedSales = hasCompleted ? raw.completedNetSales : null;

  const fwdPtFromEmp =
    hasForward &&
    raw.fwdPtCostFromEmployees != null &&
    Number.isFinite(raw.fwdPtCostFromEmployees) &&
    raw.fwdPtCostFromEmployees > 0
      ? raw.fwdPtCostFromEmployees
      : null;
  const fwdPt =
    fwdPtFromEmp != null
      ? fwdPtFromEmp
      : hasForward && raw.avgPtWage != null
        ? raw.fwdScheduledHours * raw.avgPtWage
        : hasForward
          ? 0
          : null;
  const fwdFt =
    hasForward && raw.avgFtCostPerOpenDay != null
      ? raw.avgFtCostPerOpenDay * raw.fwdDays
      : hasForward
        ? 0
        : null;
  const fwdSales =
    hasForward && raw.aov != null ? raw.fwdForecastOrders * raw.aov : hasForward ? 0 : null;

  const projectedPt =
    !hasCompleted && !hasForward
      ? null
      : (completedPt ?? 0) + (fwdPt ?? 0);
  const projectedTotal =
    !hasCompleted && !hasForward
      ? null
      : (completedTotal ?? 0) + (fwdPt ?? 0) + (fwdFt ?? 0);
  const projectedSales =
    !hasCompleted && !hasForward
      ? null
      : (completedSales ?? 0) + (fwdSales ?? 0);

  const completedPtPct = safeDiv(completedPt ?? NaN, completedSales ?? NaN);
  const completedTotalPct = safeDiv(completedTotal ?? NaN, completedSales ?? NaN);
  const projectedPtPct = safeDiv(projectedPt ?? NaN, projectedSales ?? NaN);
  const projectedTotalPct = safeDiv(projectedTotal ?? NaN, projectedSales ?? NaN);

  const completedPtAllIn = allIn(completedPt, burden);
  const completedTotalAllIn = allIn(completedTotal, burden);
  const projectedPtAllIn = allIn(projectedPt, burden);
  const projectedTotalAllIn = allIn(projectedTotal, burden);

  return {
    completedPtCost: completedPt,
    completedTotalCost: completedTotal,
    completedNetSales: completedSales,
    completedPtPct,
    completedTotalPct,
    completedDayCount: Math.max(0, Math.floor(raw.completedDayCount) || 0),
    projectedPtCost: projectedPt,
    projectedTotalCost: projectedTotal,
    projectedNetSales: projectedSales,
    projectedPtPct,
    projectedTotalPct,
    completedPtCostAllIn: completedPtAllIn,
    completedTotalCostAllIn: completedTotalAllIn,
    completedPtPctAllIn: safeDiv(completedPtAllIn ?? NaN, completedSales ?? NaN),
    completedTotalPctAllIn: safeDiv(completedTotalAllIn ?? NaN, completedSales ?? NaN),
    projectedPtCostAllIn: projectedPtAllIn,
    projectedTotalCostAllIn: projectedTotalAllIn,
    projectedPtPctAllIn: safeDiv(projectedPtAllIn ?? NaN, projectedSales ?? NaN),
    projectedTotalPctAllIn: safeDiv(projectedTotalAllIn ?? NaN, projectedSales ?? NaN),
    avgPtWage: raw.avgPtWage,
    aov: raw.aov,
    fwdDays: raw.fwdDays,
    fwdScheduledHours: raw.fwdScheduledHours,
    laborBurdenPct: burden,
    hasForward,
    hasCompleted,
  };
}
