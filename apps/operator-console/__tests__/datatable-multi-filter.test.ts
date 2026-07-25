import { describe, expect, it } from "vitest";
import { filterTextOrMulti } from "@/lib/tables/column-filter";

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

/** Faceted option set for column `forCol` given other active filters. */
function facetedValues(
  rows: Record<string, unknown>[],
  forCol: string,
  filters: { id: string; value: unknown }[],
): string[] {
  const vals = new Set<string>();
  for (const row of rows) {
    let ok = true;
    for (const f of filters) {
      if (f.id === forCol) continue;
      if (!filterTextOrMulti(row[f.id], f.value)) {
        ok = false;
        break;
      }
    }
    if (!ok) continue;
    const v = row[forCol];
    vals.add(v == null || v === "" ? "" : String(v));
  }
  return [...vals].sort((a, b) => a.localeCompare(b));
}

describe("faceted multi-select options", () => {
  const rows = [
    { category: "Logistics", category_detail: "Tolls" },
    { category: "Logistics", category_detail: "Rent / landlord" },
    { category: "Payroll / labor", category_detail: "ADP wage pay" },
  ];

  it("narrows subcategory options after category filter", () => {
    expect(
      facetedValues(rows, "category_detail", [
        { id: "category", value: ["Logistics"] },
      ]),
    ).toEqual(["Rent / landlord", "Tolls"]);
  });

  it("shows all subcategories when no other filters", () => {
    expect(facetedValues(rows, "category_detail", [])).toEqual([
      "ADP wage pay",
      "Rent / landlord",
      "Tolls",
    ]);
  });
});
