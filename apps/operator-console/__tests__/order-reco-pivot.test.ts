import { describe, expect, it } from "vitest";
import { normalizeDeliveryDate, pivotOrderRecoSlots } from "@/lib/inventory/orderRecoPivot";

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

  it("normalizeDeliveryDate truncates timestamps", () => {
    expect(normalizeDeliveryDate("2026-08-17T00:00:00.000Z")).toBe("2026-08-17");
  });
});
