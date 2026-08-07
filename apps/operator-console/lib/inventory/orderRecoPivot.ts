/**
 * Pivot long-format order-reco slot rows into one row per Item with
 * On Hand N / Order Tubs N / … / Source N columns for each live date.
 * Used by Operator Console /inventory (migration 052 N-slot model).
 */

export type OrderRecoSlotLongRow = {
  Item: string;
  Slot: number;
  delivery_date: string;
  "Current Qty": number;
  "Avg per day": number;
  "On Hand at Restock": number | null;
  "Order Tubs": number | null;
  "Order Weight lbs": number | null;
  "After Restock": number | null;
  "Days Left After Restock": number | null;
  Source: "Estimated" | "Manual" | "Actuals" | null;
  _ord: number;
};

export type OrderRecoPivotedRow = {
  Item: string;
  "Current Qty": number;
  "Avg per day": number;
  _ord: number;
  [key: string]: unknown;
};

/** Normalize BQ date / timestamp strings to YYYY-MM-DD. */
export function normalizeDeliveryDate(raw: string | null | undefined): string {
  if (!raw) return "";
  return String(raw).slice(0, 10);
}

/**
 * Build pivoted rows. `dates` is ordered slot delivery dates (YYYY-MM-DD).
 * Slot index is 1-based matching the dates array order.
 */
export function pivotOrderRecoSlots(
  dates: string[],
  longRows: OrderRecoSlotLongRow[],
): OrderRecoPivotedRow[] {
  const dateKeys = dates.map(normalizeDeliveryDate).filter(Boolean);
  const dateToSlot = new Map(dateKeys.map((d, i) => [d, i + 1]));
  const byItem = new Map<string, OrderRecoPivotedRow>();

  for (const r of longRows) {
    const d = normalizeDeliveryDate(r.delivery_date);
    const slot = dateToSlot.get(d);
    if (!slot) continue;

    let row = byItem.get(r.Item);
    if (!row) {
      row = {
        Item: r.Item,
        "Current Qty": r["Current Qty"],
        "Avg per day": r["Avg per day"],
        _ord: r._ord,
      };
      byItem.set(r.Item, row);
    }
    row[`On Hand ${slot}`] = r["On Hand at Restock"];
    row[`Order Tubs ${slot}`] = r["Order Tubs"];
    row[`Order Weight ${slot}`] = r["Order Weight lbs"];
    row[`After Restock ${slot}`] = r["After Restock"];
    row[`Days Left ${slot}`] = r["Days Left After Restock"];
    row[`Source ${slot}`] = r.Source;
  }

  return [...byItem.values()].sort((a, b) => {
    const ord = Number(a._ord) - Number(b._ord);
    if (ord !== 0) return ord;
    return Number(b["Current Qty"]) - Number(a["Current Qty"]);
  });
}
