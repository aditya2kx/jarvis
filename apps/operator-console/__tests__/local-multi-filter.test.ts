import { describe, expect, it } from "vitest";
import {
  facetedOptions,
  rowMatchesLocalFilters,
} from "@/lib/tables/localMultiFilter";

describe("facetedOptions", () => {
  it("dedupes and sorts", () => {
    expect(facetedOptions(["B", "A", "B", ""])).toEqual(["A", "B"]);
  });
});

describe("rowMatchesLocalFilters", () => {
  const row = { date: "2026-07-14", employee: "Lee, Sam" };

  it("passes when filters are all (null)", () => {
    expect(rowMatchesLocalFilters(row, { date: null, employee: null })).toBe(true);
  });

  it("filters by date multi-select", () => {
    expect(rowMatchesLocalFilters(row, { date: ["2026-07-14"], employee: null })).toBe(true);
    expect(rowMatchesLocalFilters(row, { date: ["2026-07-15"], employee: null })).toBe(false);
  });

  it("filters by employee multi-select", () => {
    expect(
      rowMatchesLocalFilters(row, { date: null, employee: ["Lee, Sam", "Other"] }),
    ).toBe(true);
    expect(rowMatchesLocalFilters(row, { date: null, employee: ["Other"] })).toBe(false);
  });

  it("none selection matches nothing", () => {
    expect(rowMatchesLocalFilters(row, { date: [], employee: null })).toBe(false);
  });
});
