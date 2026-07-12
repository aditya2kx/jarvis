/** Pure helpers for Base runway Status / stockout date (Issue #156). */

export type RunwayStatus = "Risky" | "Fine";

/**
 * Risky when no Actuals restock, or restock arrives after stockout.
 * Fine when nextRestockDate <= stockoutDate (same-day counts as Fine).
 */
export function runwayStatus(
  stockoutDate: string | null,
  nextRestockDate: string | null,
): RunwayStatus {
  if (!nextRestockDate || !stockoutDate) return "Risky";
  if (nextRestockDate > stockoutDate) return "Risky";
  return "Fine";
}

/**
 * Stockout calendar day from burn-down days left.
 * todayIso is YYYY-MM-DD (America/Chicago "today" from the caller).
 * days_left <= 0 → today; otherwise today + FLOOR(days_left).
 */
export function stockoutDateFromDaysLeft(
  daysLeft: number | null,
  todayIso: string,
): string | null {
  if (daysLeft == null || Number.isNaN(daysLeft)) return null;
  const [y, m, d] = todayIso.split("-").map(Number);
  if (!y || !m || !d) return null;
  const base = new Date(Date.UTC(y, m - 1, d));
  const addDays = daysLeft <= 0 ? 0 : Math.floor(daysLeft);
  base.setUTCDate(base.getUTCDate() + addDays);
  const yy = base.getUTCFullYear();
  const mm = String(base.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(base.getUTCDate()).padStart(2, "0");
  return `${yy}-${mm}-${dd}`;
}
