import { describe, expect, it } from "vitest";
import { runwayStatus, stockoutDateFromDaysLeft } from "@/lib/inventory/runway";

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
