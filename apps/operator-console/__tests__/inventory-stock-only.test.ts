import { describe, expect, it } from "vitest";
import { stockOnlyRows } from "@/lib/inventory/orderRecoPivot";
import type { InventoryStockLongRow } from "@/lib/inventory/orderRecoPivot";

/**
 * Regression for the blank /inventory page of 2026-09-13.
 *
 * No delivery date had been registered since 09-08, so `vw_order_reco_next_dates`
 * was empty, `refresh_order_reco` had cleared `inventory_order_reco`, and the
 * page rendered "No rows." — while BQ knew that Açaí had 5.9 days of stock left
 * and Blade 3.0. Stock and burn rate do not depend on a delivery date, so they
 * must survive its absence.
 */

const LIVE: InventoryStockLongRow[] = [
  { Item: "Blade", "Current Qty": 2.0, "Avg per day": 0.66, "Days left": 3.0 },
  { Item: "Açaí", "Current Qty": 9.8, "Avg per day": 1.65, "Days left": 5.9 },
  { Item: "Pitaya", "Current Qty": 10.8, "Avg per day": 0.76, "Days left": 14.2 },
];

describe("stockOnlyRows", () => {
  it("keeps the rows that do not depend on a delivery date", () => {
    const rows = stockOnlyRows(LIVE);
    expect(rows).toHaveLength(3);
    expect(rows[0]).toMatchObject({
      Item: "Blade",
      "Current Qty": 2.0,
      "Avg per day": 0.66,
      "Days left": 3.0,
    });
  });

  it("emits no per-slot ordering columns", () => {
    // Order Tubs / On Hand / After Restock are all defined relative to a
    // delivery date. Inventing them without one would be making numbers up.
    const keys = Object.keys(stockOnlyRows(LIVE)[0]);
    expect(keys.some((k) => /^(On Hand|Order Tubs|After Restock|Source) /.test(k))).toBe(
      false,
    );
  });

  it("preserves the incoming order, so the most urgent base stays first", () => {
    // The query orders by Days left ASC; the mapping must not resort it.
    expect(stockOnlyRows(LIVE).map((r) => r.Item)).toEqual(["Blade", "Açaí", "Pitaya"]);
  });

  it("passes a null Days left through instead of coercing it to zero", () => {
    // A base with no usage history has unknown runway, not zero runway —
    // zero would render as an emergency that is not happening.
    const rows = stockOnlyRows([
      { Item: "Ube", "Current Qty": 3.5, "Avg per day": 0, "Days left": null },
    ]);
    expect(rows[0]["Days left"]).toBeNull();
  });

  it("survives an empty source without throwing", () => {
    expect(stockOnlyRows([])).toEqual([]);
  });
});
