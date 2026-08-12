import type { ColumnDef } from "@tanstack/react-table";
import type { RestockActualsRow } from "@/lib/bq/queries";
import { normalizeDeliveryDate } from "@/lib/inventory/orderRecoPivot";
import { ACTIVE_BASES } from "@/lib/restock/parse";

export type RestockActualsPivotedRow = {
  date: string;
  TOTAL: number;
  [base: string]: string | number;
};

/**
 * Pivot long actuals rows into one row per delivery date.
 * Column order is ACTIVE_BASES (not locale sort). Unknown items (e.g. Blade)
 * are ignored. Missing bases on a date that has actuals become 0.
 */
export function pivotRestockActuals(
  rows: RestockActualsRow[],
  bases: readonly string[] = ACTIVE_BASES,
): RestockActualsPivotedRow[] {
  const baseSet = new Set(bases);
  const byDate = new Map<string, Map<string, number>>();

  for (const r of rows) {
    const d = normalizeDeliveryDate(r.delivery_date);
    if (!d || !baseSet.has(r.item)) continue;
    if (!byDate.has(d)) byDate.set(d, new Map());
    const qty = r.quantity_tubs == null || Number.isNaN(Number(r.quantity_tubs))
      ? 0
      : Number(r.quantity_tubs);
    byDate.get(d)!.set(r.item, qty);
  }

  const dates = [...byDate.keys()].sort((a, b) => (a < b ? 1 : a > b ? -1 : 0));
  return dates.map((date) => {
    const items = byDate.get(date)!;
    const row: RestockActualsPivotedRow = { date, TOTAL: 0 };
    let total = 0;
    for (const base of bases) {
      const n = items.has(base) ? Number(items.get(base)) : 0;
      row[base] = n;
      total += n;
    }
    row.TOTAL = total;
    return row;
  });
}

export function restockActualsColumns(
  bases: readonly string[] = ACTIVE_BASES,
): ColumnDef<RestockActualsPivotedRow>[] {
  return [
    { accessorKey: "date", header: "Date", meta: { format: { kind: "date" } } },
    ...bases.map((base) => ({
      accessorKey: base,
      header: base,
      meta: { format: { kind: "number", digits: 0 } },
    })),
    {
      accessorKey: "TOTAL",
      header: "TOTAL",
      meta: { format: { kind: "number", digits: 0 } },
    },
  ];
}
