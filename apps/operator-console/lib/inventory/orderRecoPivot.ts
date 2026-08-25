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
  refreshed_at?: string | null;
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

function tubsOf(row: OrderRecoSlotLongRow): number {
  return Number(row["Order Tubs"] ?? 0);
}

/** Dates in `rows` whose item Order Tubs sum to the TOTAL row. */
export function completeDatesForRows(rows: OrderRecoSlotLongRow[]): string[] {
  const byDate = new Map<string, OrderRecoSlotLongRow[]>();
  for (const r of rows) {
    const d = normalizeDeliveryDate(r.delivery_date);
    if (!d) continue;
    if (!byDate.has(d)) byDate.set(d, []);
    byDate.get(d)!.push(r);
  }
  const out: string[] = [];
  for (const [d, group] of byDate) {
    const total = group.find((r) => r.Item === "TOTAL");
    const items = group.filter((r) => r.Item !== "TOTAL");
    if (!total || items.length === 0) continue;
    const sum = items.reduce((acc, r) => acc + tubsOf(r), 0);
    if (sum === tubsOf(total)) out.push(d);
  }
  return out.sort();
}

export type PaintGeneration = {
  refreshedAt: string | null;
  readyDates: string[];
  pending: boolean;
};

/**
 * Prefer a fully consistent reco generation that covers the most live dates.
 * Incomplete generations (mid write-then-swap) lose to an older complete one.
 */
export function selectPaintGeneration(
  liveDates: string[],
  longRows: OrderRecoSlotLongRow[],
): PaintGeneration {
  const live = [...new Set(liveDates.map(normalizeDeliveryDate).filter(Boolean))].sort();
  const liveSet = new Set(live);
  const byGen = new Map<string, OrderRecoSlotLongRow[]>();
  for (const r of longRows) {
    const g = r.refreshed_at == null || r.refreshed_at === "" ? "_none" : String(r.refreshed_at);
    if (!byGen.has(g)) byGen.set(g, []);
    byGen.get(g)!.push(r);
  }
  let best: { score: number; ts: string; dates: string[] } | null = null;
  for (const [ts, rows] of byGen) {
    const complete = completeDatesForRows(rows).filter((d) => liveSet.has(d));
    const score = complete.length;
    if (
      !best ||
      score > best.score ||
      (score === best.score && ts > best.ts)
    ) {
      best = { score, ts, dates: complete };
    }
  }
  const readyDates = best?.dates ?? [];
  const pending = live.some((d) => !readyDates.includes(d));
  return {
    refreshedAt: best && best.ts !== "_none" ? best.ts : null,
    readyDates,
    pending,
  };
}

/** Rows that belong to `paint.refreshedAt` (or null-timestamp rows when unset). */
export function rowsForPaintGeneration(
  longRows: OrderRecoSlotLongRow[],
  paint: PaintGeneration,
): OrderRecoSlotLongRow[] {
  if (paint.refreshedAt == null) {
    return longRows.filter((r) => r.refreshed_at == null || r.refreshed_at === "");
  }
  const want = String(paint.refreshedAt);
  return longRows.filter((r) => String(r.refreshed_at) === want);
}
