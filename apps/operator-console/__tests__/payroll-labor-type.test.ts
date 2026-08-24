import { describe, expect, it } from "vitest";
import { rowMatchesLaborType } from "@/lib/payroll/laborBucket";

const rows = [
  { employee: "Garcia, Jacob", labor_type: "Part-time" },
  { employee: "Krause, Lindsay", labor_type: "Full-time" },
];

describe("payroll labor type filter", () => {
  it("default All includes Lindsay", () => {
    const shown = rows.filter((r) => rowMatchesLaborType(r.labor_type, null));
    expect(shown.map((r) => r.employee)).toContain("Krause, Lindsay");
  });

  it("Part-time hides Lindsay", () => {
    const shown = rows.filter((r) =>
      rowMatchesLaborType(r.labor_type, ["Part-time"]),
    );
    expect(shown.map((r) => r.employee)).toEqual(["Garcia, Jacob"]);
  });
});
