import { describe, expect, it } from "vitest";
import {
  facetedMultiOptions,
  filterTextOrMulti,
} from "@/lib/tables/column-filter";

describe("filterTextOrMulti", () => {
  it("multi-select matches any selected exact value", () => {
    expect(filterTextOrMulti("Payroll / labor", ["Payroll / labor", "Rent"])).toBe(true);
    expect(filterTextOrMulti("Other", ["Payroll / labor"])).toBe(false);
    expect(filterTextOrMulti("Payroll / labor", [])).toBe(true);
  });

  it("text filter is case-insensitive substring", () => {
    expect(filterTextOrMulti("DD *DOORDASH", "door")).toBe(true);
    expect(filterTextOrMulti("DD *DOORDASH", "UBER")).toBe(false);
  });
});

describe("facetedMultiOptions", () => {
  const rows = [
    { category: "Logistics", category_detail: "Tolls" },
    { category: "Logistics", category_detail: "Rent / landlord" },
    { category: "Payroll / labor", category_detail: "ADP wage pay" },
  ];

  it("narrows subcategory options after category filter", () => {
    expect(
      facetedMultiOptions(rows, "category_detail", [
        { id: "category", value: ["Logistics"] },
      ]),
    ).toEqual(["Rent / landlord", "Tolls"]);
  });

  it("shows all subcategories when no other filters", () => {
    expect(facetedMultiOptions(rows, "category_detail", [])).toEqual([
      "ADP wage pay",
      "Rent / landlord",
      "Tolls",
    ]);
  });

  it("retains selected values even if they drop out of the facet", () => {
    expect(
      facetedMultiOptions(
        rows,
        "category_detail",
        [{ id: "category", value: ["Logistics"] }],
        ["ADP wage pay"],
      ),
    ).toEqual(["ADP wage pay", "Rent / landlord", "Tolls"]);
  });
});
