import { describe, expect, it } from "vitest";
import {
  parseSalesStat,
  parseRollupStat,
  salesStatApplicable,
  rollupStatApplicable,
  SALES_STAT_OPTIONS,
  ROLLUP_STAT_OPTIONS,
} from "@/lib/filters/sales-stat";

describe("parseSalesStat", () => {
  it("defaults to avg; only explicit total sticks", () => {
    expect(parseSalesStat(undefined)).toBe("avg");
    expect(parseSalesStat("avg")).toBe("avg");
    expect(parseSalesStat("total")).toBe("total");
    expect(parseSalesStat("bogus")).toBe("avg");
  });

  it("Rollup aliases match Sales (Labor reuses the same control)", () => {
    expect(parseRollupStat("total")).toBe("total");
    expect(rollupStatApplicable("hour")).toBe(true);
    expect(ROLLUP_STAT_OPTIONS).toBe(SALES_STAT_OPTIONS);
  });
});

describe("salesStatApplicable", () => {
  it("only weekday and hour", () => {
    expect(salesStatApplicable("weekday")).toBe(true);
    expect(salesStatApplicable("hour")).toBe(true);
    expect(salesStatApplicable("day")).toBe(false);
    expect(salesStatApplicable("week")).toBe(false);
    expect(salesStatApplicable("all")).toBe(false);
  });
});

describe("SALES_STAT_OPTIONS", () => {
  it("is Average then Total", () => {
    expect(SALES_STAT_OPTIONS.map((o) => o.value)).toEqual(["avg", "total"]);
  });
});
