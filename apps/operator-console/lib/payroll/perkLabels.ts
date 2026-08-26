/** Parse vw_model_payroll_period.perk_reason into named line items. */

export type PerkItem = {
  id: string;
  label: string;
  dollars: number | null;
};

const PERK_LABELS: Record<string, string> = {
  gym: "Gym",
  mileage: "Mileage",
  food_handler: "Food handler cert",
  other: "Other reimbursement",
};

export function labelPerkId(id: string): string {
  const key = id.trim();
  if (PERK_LABELS[key]) return PERK_LABELS[key];
  const cleaned = key.replace(/[_-]+/g, " ");
  if (!cleaned) return id;
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1);
}

/**
 * Accepts:
 * - `gym:20;phone:15` (migration 060)
 * - `gym; phone` or `gym` (migration 059 STRING_AGG of perk_id)
 */
export function parsePerkReasons(raw: string | null | undefined): PerkItem[] {
  if (!raw || !String(raw).trim()) return [];
  const parts = String(raw)
    .split(/;|\s·\s/)
    .map((s) => s.trim())
    .filter(Boolean);
  return parts.map((part) => {
    const colon = part.match(/^([^:]+):(\d+(?:\.\d+)?)$/);
    if (colon) {
      return {
        id: colon[1],
        label: labelPerkId(colon[1]),
        dollars: Number(colon[2]),
      };
    }
    return { id: part, label: labelPerkId(part), dollars: null };
  });
}
