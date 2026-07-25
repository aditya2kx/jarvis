import { describe, expect, it } from "vitest";
import { effectiveExclude, PAYROLL_LABOR_CATEGORY_ID } from "@/lib/plaid/exclude-accounting";

/** Pure helpers used by Home Cost/Labor twin (#189). */
describe("home accounting helpers", () => {
  it("exports payroll parent id constant", () => {
    expect(PAYROLL_LABOR_CATEGORY_ID).toBe("payroll_labor");
  });

  it("internal transfers parent excludes by default shape", () => {
    expect(
      effectiveExclude(
        {
          id: "internal_transfers",
          parent_id: null,
          exclude_from_accounting: true,
        },
        null,
      ),
    ).toBe(true);
  });
});
