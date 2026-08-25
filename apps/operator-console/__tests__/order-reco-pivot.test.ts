import { describe, expect, it } from "vitest";
import {
  normalizeDeliveryDate,
  pivotOrderRecoSlots,
  rowsForPaintGeneration,
  selectPaintGeneration,
} from "@/lib/inventory/orderRecoPivot";
import type { OrderRecoSlotLongRow } from "@/lib/inventory/orderRecoPivot";

function row(
  partial: Partial<OrderRecoSlotLongRow> & Pick<OrderRecoSlotLongRow, "Item" | "delivery_date">,
): OrderRecoSlotLongRow {
  return {
    Slot: 1,
    "Current Qty": 1,
    "Avg per day": 1,
    "On Hand at Restock": 1,
    "Order Tubs": 0,
    "Order Weight lbs": 0,
    "After Restock": 1,
    "Days Left After Restock": 1,
    Source: "Estimated",
    _ord: partial.Item === "TOTAL" ? 1 : 0,
    refreshed_at: "2026-08-24T00:00:00Z",
    ...partial,
  };
}

describe("pivotOrderRecoSlots", () => {
  it("pivots three slots into On Hand N / Source N columns", () => {
    const dates = ["2026-08-03", "2026-08-10", "2026-08-17"];
    const rows = pivotOrderRecoSlots(dates, [
      {
        Item: "Açaí",
        Slot: 1,
        delivery_date: "2026-08-03",
        "Current Qty": 10,
        "Avg per day": 2,
        "On Hand at Restock": 10,
        "Order Tubs": 14,
        "Order Weight lbs": 252,
        "After Restock": 24,
        "Days Left After Restock": 12,
        Source: "Actuals",
        _ord: 0,
      },
      {
        Item: "Açaí",
        Slot: 2,
        delivery_date: "2026-08-10T00:00:00",
        "Current Qty": 10,
        "Avg per day": 2,
        "On Hand at Restock": 8,
        "Order Tubs": 15,
        "Order Weight lbs": 270,
        "After Restock": 23,
        "Days Left After Restock": 11,
        Source: "Actuals",
        _ord: 0,
      },
      {
        Item: "Açaí",
        Slot: 3,
        delivery_date: "2026-08-17",
        "Current Qty": 10,
        "Avg per day": 2,
        "On Hand at Restock": 5,
        "Order Tubs": 20,
        "Order Weight lbs": 400,
        "After Restock": 25,
        "Days Left After Restock": 12.5,
        Source: "Estimated",
        _ord: 0,
      },
      {
        Item: "TOTAL",
        Slot: 1,
        delivery_date: "2026-08-03",
        "Current Qty": 83,
        "Avg per day": 10,
        "On Hand at Restock": 83,
        "Order Tubs": 69,
        "Order Weight lbs": 1400,
        "After Restock": 152,
        "Days Left After Restock": 15,
        Source: "Actuals",
        _ord: 1,
      },
    ]);

    expect(rows).toHaveLength(2);
    expect(rows[0].Item).toBe("Açaí");
    expect(rows[0]["On Hand 1"]).toBe(10);
    expect(rows[0]["On Hand 3"]).toBe(5);
    expect(rows[0]["Source 3"]).toBe("Estimated");
    expect(rows[0]["Order Weight 1"]).toBe(252);
    expect(rows[0]["Order Weight 2"]).toBe(270);
    expect(rows[1].Item).toBe("TOTAL");
    expect(rows[1]["Order Tubs 1"]).toBe(69);
    expect(rows[1]["Order Weight 1"]).toBe(1400);
  });

  it("preserves Manual source on pivoted rows", () => {
    const rows = pivotOrderRecoSlots(["2026-08-20"], [
      {
        Item: "Ube",
        Slot: 1,
        delivery_date: "2026-08-20",
        "Current Qty": 4,
        "Avg per day": 1,
        "On Hand at Restock": 4,
        "Order Tubs": 0,
        "Order Weight lbs": 0,
        "After Restock": 4,
        "Days Left After Restock": 4,
        Source: "Manual",
        _ord: 0,
      },
    ]);
    expect(rows[0]["Source 1"]).toBe("Manual");
    expect(rows[0]["Order Tubs 1"]).toBe(0);
  });

  it("normalizeDeliveryDate truncates timestamps", () => {
    expect(normalizeDeliveryDate("2026-08-17T00:00:00.000Z")).toBe("2026-08-17");
  });

  it("selectPaintGeneration omits a live date until item tubs sum to TOTAL", () => {
    const live = ["2026-08-28", "2026-09-04"];
    const old = [
      row({ Item: "Açaí", delivery_date: "2026-08-28", "Order Tubs": 13, refreshed_at: "t0" }),
      row({ Item: "TOTAL", delivery_date: "2026-08-28", "Order Tubs": 13, refreshed_at: "t0" }),
    ];
    const mid = [
      ...old,
      row({ Item: "Açaí", delivery_date: "2026-09-04", "Order Tubs": 0, refreshed_at: "t1" }),
      row({ Item: "TOTAL", delivery_date: "2026-09-04", "Order Tubs": 33, refreshed_at: "t1" }),
    ];
    const torn = selectPaintGeneration(live, mid);
    expect(torn.readyDates).toEqual(["2026-08-28"]);
    expect(torn.pending).toBe(true);

    const done = [
      row({ Item: "Açaí", delivery_date: "2026-08-28", "Order Tubs": 13, refreshed_at: "t2" }),
      row({ Item: "TOTAL", delivery_date: "2026-08-28", "Order Tubs": 13, refreshed_at: "t2" }),
      row({ Item: "Açaí", delivery_date: "2026-09-04", "Order Tubs": 22, refreshed_at: "t2" }),
      row({ Item: "Matcha", delivery_date: "2026-09-04", "Order Tubs": 11, refreshed_at: "t2" }),
      row({ Item: "TOTAL", delivery_date: "2026-09-04", "Order Tubs": 33, refreshed_at: "t2" }),
    ];
    const ready = selectPaintGeneration(live, done);
    expect(ready.readyDates).toEqual(["2026-08-28", "2026-09-04"]);
    expect(ready.pending).toBe(false);
  });

  it("prefers an older complete generation over a newer partial one", () => {
    const live = ["2026-08-28", "2026-09-04"];
    const rows = [
      row({ Item: "Açaí", delivery_date: "2026-08-28", "Order Tubs": 10, refreshed_at: "2026-08-24T10:00:00Z" }),
      row({ Item: "TOTAL", delivery_date: "2026-08-28", "Order Tubs": 10, refreshed_at: "2026-08-24T10:00:00Z" }),
      row({ Item: "Açaí", delivery_date: "2026-08-28", "Order Tubs": 10, refreshed_at: "2026-08-24T11:00:00Z" }),
      row({ Item: "TOTAL", delivery_date: "2026-08-28", "Order Tubs": 10, refreshed_at: "2026-08-24T11:00:00Z" }),
    ];
    const paint = selectPaintGeneration(live, rows);
    expect(paint.readyDates).toEqual(["2026-08-28"]);
    expect(paint.pending).toBe(true);
  });

  it("pivots only the selected generation when two gens share a live date", () => {
    const live = ["2026-08-28", "2026-09-04"];
    const mixed = [
      row({ Item: "Açaí", delivery_date: "2026-08-28", "Order Tubs": 10, refreshed_at: "t1" }),
      row({ Item: "TOTAL", delivery_date: "2026-08-28", "Order Tubs": 10, refreshed_at: "t1" }),
      row({ Item: "Açaí", delivery_date: "2026-09-04", "Order Tubs": 0, refreshed_at: "t1" }),
      row({ Item: "TOTAL", delivery_date: "2026-09-04", "Order Tubs": 33, refreshed_at: "t1" }),
      row({ Item: "Açaí", delivery_date: "2026-08-28", "Order Tubs": 13, refreshed_at: "t0" }),
      row({ Item: "TOTAL", delivery_date: "2026-08-28", "Order Tubs": 13, refreshed_at: "t0" }),
    ];
    const paint = selectPaintGeneration(live, mixed);
    expect(paint.refreshedAt).toBe("t1");
    expect(paint.readyDates).toEqual(["2026-08-28"]);
    const unfiltered = pivotOrderRecoSlots(paint.readyDates, mixed);
    expect(unfiltered.find((r) => r.Item === "Açaí")?.["Order Tubs 1"]).toBe(13);
    const pivoted = pivotOrderRecoSlots(
      paint.readyDates,
      rowsForPaintGeneration(mixed, paint),
    );
    expect(pivoted.find((r) => r.Item === "Açaí")?.["Order Tubs 1"]).toBe(10);
    expect(pivoted.find((r) => r.Item === "TOTAL")?.["Order Tubs 1"]).toBe(10);
  });
});
