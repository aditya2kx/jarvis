/** recognition_bonuses.pay_period key (migration 033): start..end. */
export function payPeriodKey(periodStart: string, periodEnd: string): string {
  return `${periodStart}..${periodEnd}`;
}

/** Mirror vw_model_payroll_period bonus / total math (migration 049). Dollars. */
export function estTotalPayDollars(parts: {
  estGrossPay: number;
  tipsAllocated: number;
  reviewBonus: number;
  recognitionBonus: number;
}): number {
  return round2(
    (parts.estGrossPay ?? 0) +
      (parts.tipsAllocated ?? 0) +
      (parts.reviewBonus ?? 0) +
      (parts.recognitionBonus ?? 0),
  );
}

export function bonusDiffDollars(parts: {
  reviewBonus: number;
  recognitionBonus: number;
  adpBonusPaid: number | null | undefined;
}): number {
  return round2(
    (parts.reviewBonus ?? 0) +
      (parts.recognitionBonus ?? 0) -
      (parts.adpBonusPaid ?? 0),
  );
}

/** Join reasons the same way as STRING_AGG(..., '; ' ORDER BY …). */
export function concatRecognitionReasons(reasons: Array<string | null | undefined>): string {
  return reasons
    .map((r) => (r ?? "").trim())
    .filter(Boolean)
    .join("; ");
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}
