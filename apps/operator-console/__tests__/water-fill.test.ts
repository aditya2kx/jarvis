import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { waterFillDate, type WaterFillBase } from "@/lib/inventory/waterFill";

// Shared with core/test_order_reco_reference.py so the drawer preview and the
// Python reference (and, through sandbox parity, the BQ procedure) agree.
const golden = JSON.parse(
  readFileSync(path.resolve(__dirname, "../../../core/testdata/order_reco_waterfill_golden.json"), "utf8"),
) as {
  bases: WaterFillBase[];
  cases: {
    name: string;
    bases?: WaterFillBase[];
    capacity: number;
    pins: Record<string, number>;
    expected: Record<string, number>;
  }[];
};

describe("waterFillDate golden fixture", () => {
  for (const c of golden.cases) {
    it(c.name, () => {
      expect(waterFillDate(c.bases ?? golden.bases, c.capacity, c.pins)).toEqual(c.expected);
    });
  }

  it("never exceeds capacity when pins fit", () => {
    const onHand = golden.bases.reduce((a, b) => a + b.onHand, 0);
    for (const cap of [90, 110, 112, 140]) {
      const total = Object.values(waterFillDate(golden.bases, cap, {})).reduce((a, n) => a + n, 0);
      expect(onHand + total).toBeLessThanOrEqual(cap);
    }
  });
});
