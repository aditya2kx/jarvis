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
