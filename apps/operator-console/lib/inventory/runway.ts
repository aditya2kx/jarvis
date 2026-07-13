/** Pure helpers for Base runway Status / stockout dates (Issues #156 / #164). */

export type RunwayStatus = "Risky" | "Fine";

/**
 * Risky when no Actuals restock, or restock arrives after stockout.
 * Fine when nextRestockDate <= stockoutDate (same-day counts as Fine).
 * Used for Status 1 and Status 2 (caller passes null restock for missing slot 2 → Risky;
 * page maps null Status 2 when restock_2 is absent).
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
 * Status for a slot: Actuals-only Fine (qty must be present, including 0).
 * Missing restock date → Risky for slot 1; caller uses null Status 2 when no slot 2.
 */
export function runwayStatusWithQty(
  stockoutDate: string | null,
  restockDate: string | null,
  qty: number | null,
): RunwayStatus {
  if (!restockDate || qty == null || !stockoutDate) return "Risky";
  return runwayStatus(stockoutDate, restockDate);
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

function parseIso(iso: string): Date | null {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return null;
  return new Date(Date.UTC(y, m - 1, d));
}

function formatIso(d: Date): string {
  const yy = d.getUTCFullYear();
  const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(d.getUTCDate()).padStart(2, "0");
  return `${yy}-${mm}-${dd}`;
}

/**
 * Stockout 2: chain after slot-1 restock (mirrors BQ migration 036).
 * on_hand_at_d1 = max(0, currentQty − days_to_d1 × vel)
 * after_d1 = on_hand_at_d1 + COALESCE(qty1, 0)
 * stockout_2 = d1 + FLOOR(after_d1 / vel); d1 when days-after <= 0.
 */
export function stockout2AfterSlot1(
  currentQty: number,
  vel: number,
  d1Iso: string,
  qty1: number | null,
  todayIso: string,
): string | null {
  if (!d1Iso || vel <= 0) return null;
  const today = parseIso(todayIso);
  const d1 = parseIso(d1Iso);
  if (!today || !d1) return null;
  const daysToD1 = Math.round((d1.getTime() - today.getTime()) / 86_400_000);
  const onHandAtD1 = Math.max(0, currentQty - daysToD1 * vel);
  const afterD1 = onHandAtD1 + (qty1 ?? 0);
  const daysAfter = afterD1 / vel;
  const addDays = daysAfter <= 0 ? 0 : Math.floor(daysAfter);
  const out = new Date(d1.getTime());
  out.setUTCDate(out.getUTCDate() + addDays);
  return formatIso(out);
}
