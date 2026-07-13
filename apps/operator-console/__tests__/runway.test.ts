import { describe, expect, it } from "vitest";
import {
  runwayStatus,
  runwayStatusWithQty,
  stockout2AfterSlot1,
  stockoutDateFromDaysLeft,
} from "@/lib/inventory/runway";

describe("runwayStatus", () => {
  it("Risky when no restock", () => {
    expect(runwayStatus("2026-07-20", null)).toBe("Risky");
  });

  it("Risky when no stockout date", () => {
    expect(runwayStatus(null, "2026-07-15")).toBe("Risky");
  });

  it("Risky when restock after stockout", () => {
    expect(runwayStatus("2026-07-20", "2026-07-25")).toBe("Risky");
  });

  it("Fine when restock on stockout date", () => {
    expect(runwayStatus("2026-07-20", "2026-07-20")).toBe("Fine");
  });

  it("Fine when restock before stockout", () => {
    expect(runwayStatus("2026-07-20", "2026-07-15")).toBe("Fine");
  });
});

describe("runwayStatusWithQty", () => {
  it("Risky when qty missing (Estimated-only slot)", () => {
    expect(runwayStatusWithQty("2026-07-20", "2026-07-16", null)).toBe("Risky");
  });

  it("Fine when qty 0 Actuals and date on time", () => {
    expect(runwayStatusWithQty("2026-07-20", "2026-07-16", 0)).toBe("Fine");
  });

  it("Fine when Actuals qty present and restock before stockout", () => {
    expect(runwayStatusWithQty("2026-07-20", "2026-07-16", 17)).toBe("Fine");
  });
});

describe("stockoutDateFromDaysLeft", () => {
  const today = "2026-07-12";

  it("returns null for null days left", () => {
    expect(stockoutDateFromDaysLeft(null, today)).toBeNull();
  });

  it("returns today when days left <= 0", () => {
    expect(stockoutDateFromDaysLeft(0, today)).toBe("2026-07-12");
    expect(stockoutDateFromDaysLeft(-1, today)).toBe("2026-07-12");
  });

  it("floors fractional days left", () => {
    expect(stockoutDateFromDaysLeft(7.9, today)).toBe("2026-07-19");
    expect(stockoutDateFromDaysLeft(7.0, today)).toBe("2026-07-19");
  });

  it("adds whole days from today", () => {
    expect(stockoutDateFromDaysLeft(3.2, today)).toBe("2026-07-15");
  });
});

describe("stockout2AfterSlot1", () => {
  const today = "2026-07-13";
  const d1 = "2026-07-16";

  it("returns null when vel is 0", () => {
    expect(stockout2AfterSlot1(10, 0, d1, 5, today)).toBeNull();
  });

  it("chains on_hand_at_d1 + qty1 from d1", () => {
    // days_to_d1 = 3; on_hand = max(0, 10 - 3*2) = 4; after = 4+6 = 10; days = 5 → 7/21
    expect(stockout2AfterSlot1(10, 2, d1, 6, today)).toBe("2026-07-21");
  });

  it("uses 0 when qty1 null", () => {
    // on_hand = 4; after = 4; days = 2 → 7/18
    expect(stockout2AfterSlot1(10, 2, d1, null, today)).toBe("2026-07-18");
  });

  it("stockout on d1 when after_d1 is 0", () => {
    // current burns to 0 by d1; qty1 = 0 → days_after = 0 → d1
    expect(stockout2AfterSlot1(6, 2, d1, 0, today)).toBe("2026-07-16");
  });
});
