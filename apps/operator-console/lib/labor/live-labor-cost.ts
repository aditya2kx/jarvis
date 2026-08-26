/**
 * Canonical labor $ / % rules for Operator Console (Issue #267).
 * BQ `vw_labor_daily_live` / hour-grain SQL must match this bucket + cost math.
 * Frozen `model_labor_daily` dollars are not a presentation source.
 */

export function isFullTimeLaborBucket(opts: {
  isSalaried?: boolean | null;
  excludedFromLaborPct?: boolean | null;
}): boolean {
  return Boolean(opts.isSalaried || opts.excludedFromLaborPct);
}

export function shiftLaborDollars(
  hours: number,
  wageRateDollars: number,
  opts: { isSalaried?: boolean | null; excludedFromLaborPct?: boolean | null },
): { hourlyCost: number; fulltimeCost: number } {
  const cost = hours * wageRateDollars;
  if (isFullTimeLaborBucket(opts)) {
    return { hourlyCost: 0, fulltimeCost: cost };
  }
  return { hourlyCost: cost, fulltimeCost: 0 };
}

/** Labor $ ÷ Square net sales as a 0–1 fraction. */
export function laborPctOfNetSales(laborDollars: number, netSales: number): number | null {
  if (!(netSales > 0) || Number.isNaN(laborDollars) || Number.isNaN(netSales)) return null;
  return laborDollars / netSales;
}
