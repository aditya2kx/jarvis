/** PT/FT bucket matching vw_model_payroll_period (migration 062). */

export function laborTypeForEmployee(flags: {
  isSalaried?: boolean | null;
  excludedFromLaborPct?: boolean | null;
  excludedFromTipPool?: boolean | null;
}): "Full-time" | "Part-time" {
  if (flags.isSalaried || flags.excludedFromLaborPct || flags.excludedFromTipPool) {
    return "Full-time";
  }
  return "Part-time";
}

/** URL labor_type selection: null = all, [] = none, else named buckets. */
export function rowMatchesLaborType(
  laborType: string | null | undefined,
  selected: string[] | null,
): boolean {
  if (selected == null) return true;
  if (selected.length === 0) return false;
  return selected.includes(laborType ?? "");
}
