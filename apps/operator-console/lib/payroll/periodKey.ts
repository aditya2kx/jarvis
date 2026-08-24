/** recognition_bonuses.pay_period key (migration 033): start..end. */
export function payPeriodKey(periodStart: string, periodEnd: string): string {
  return `${periodStart}..${periodEnd}`;
}

/** Regular+OT wage dollars (migration 064 ADP half-up). Null rate → null. */
export function estGrossPayDollars(parts: {
  hoursWorked: number;
  otHours: number;
  wageRate: number | null;
  otRate: number | null;
}): number | null {
  if (parts.wageRate == null) return null;
  const ot = Math.max(0, parts.otHours ?? 0);
  const reg = Math.max(0, (parts.hoursWorked ?? 0) - ot);
  const otRate = parts.otRate ?? parts.wageRate * 1.5;
  return adpWageDollars(reg, parts.wageRate, ot, otRate);
}

/** Mirror vw_model_payroll_period bonus / total math (migration 049+059). Dollars. */
export function estTotalPayDollars(parts: {
  estGrossPay: number;
  tipsAllocated: number;
  reviewBonus: number;
  recognitionBonus: number;
  perks?: number;
}): number {
  return round2(
    (parts.estGrossPay ?? 0) +
      (parts.tipsAllocated ?? 0) +
      (parts.reviewBonus ?? 0) +
      (parts.recognitionBonus ?? 0) +
      (parts.perks ?? 0),
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

/** Hundredths of a dollar amount (47.30 → 4730) without a float ×100 error. */
function toHundredths(n: number): number {
  const s = n.toFixed(2);
  const neg = s.startsWith("-");
  const [w, f] = (neg ? s.slice(1) : s).split(".");
  const v = parseInt(w, 10) * 100 + parseInt(f, 10);
  return neg ? -v : v;
}

/**
 * ADP Preview: ROUND_HALF_UP(reg×rate + ot×otRate, 2).
 * Integer path: (hh×rc + …) / 100 cents, then Math.round (.5 → away for +).
 */
function adpWageDollars(
  regularHours: number,
  wageRate: number,
  otHours: number,
  otRate: number,
): number {
  const prod =
    toHundredths(regularHours) * toHundredths(wageRate) +
    toHundredths(otHours) * toHundredths(otRate);
  return Math.round(prod / 100) / 100;
}
