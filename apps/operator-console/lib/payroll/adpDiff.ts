/** Classify est vs ADP paid columns on a closed payroll period. */

export type AdpDiffKind = "match" | "variance" | "not_on_check";

const EPS = 0.005;

export function classifyAdpDiff(
  paid: number | null | undefined,
  diff: number | null | undefined,
): AdpDiffKind {
  if (paid == null) return "not_on_check";
  if (Math.abs(diff ?? 0) < EPS) return "match";
  return "variance";
}
