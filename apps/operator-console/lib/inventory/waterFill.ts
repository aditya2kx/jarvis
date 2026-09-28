/**
 * One delivery date's order water-fill, for the instant "what will Apply do"
 * preview in the Order Tubs drawer (Issue #350). Mirrors
 * core/order_reco_reference.py::_water_fill and migration 081; the shared
 * fixture core/testdata/order_reco_waterfill_golden.json keeps them equal.
 *
 * Pins on a date don't change that date's on-hand at arrival, so the live
 * table's `On Hand N` is the right input. It is rounded to 2 decimals, so a
 * preview can differ from the committed refresh by a tub at an exact tie.
 */

export type WaterFillBase = { item: string; onHand: number; avgPerDay: number };

const MAX_EXTRA_TUBS_PER_ITEM = 300;
const NOT_WATER_FILLED = new Set(["Blade"]);

export function waterFillDate(
  bases: WaterFillBase[],
  capacity: number,
  pins: Record<string, number>,
): Record<string, number> {
  const pinned = Object.values(pins).reduce((a, n) => a + n, 0);
  const onHand = bases.reduce((a, b) => a + b.onHand, 0);
  const budget = Math.max(Math.floor(capacity - onHand) - pinned, 0);

  const cands: { key: number; item: string; k: number }[] = [];
  for (const b of bases) {
    if (NOT_WATER_FILLED.has(b.item) || b.avgPerDay <= 0 || b.item in pins) continue;
    for (let k = 1; k <= Math.min(budget, MAX_EXTRA_TUBS_PER_ITEM); k++) {
      cands.push({ key: (b.onHand + k - 1) / b.avgPerDay, item: b.item, k });
    }
  }
  cands.sort((a, b) => a.key - b.key || (a.item < b.item ? -1 : a.item > b.item ? 1 : 0) || a.k - b.k);

  const out: Record<string, number> = {};
  for (const b of bases) out[b.item] = pins[b.item] ?? 0;
  for (const c of cands.slice(0, budget)) out[c.item] += 1;
  return out;
}
