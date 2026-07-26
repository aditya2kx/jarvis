import { describe, expect, it } from "vitest";
import {
  cellKey,
  formatDelta,
  formatQty,
  groupUsageDayAudit,
  pivotUsageDayAudit,
  previewLine,
  statusTag,
} from "@/lib/inventory/usageDayAudit";
import type { UsageDayAuditRow } from "@/lib/bq/queries";

function row(partial: Partial<UsageDayAuditRow> & Pick<UsageDayAuditRow, "item" | "submitted_date" | "status">): UsageDayAuditRow {
  return {
    store: "palmetto",
    qty: 10,
    delta: -1,
    rule_eligible: true,
    in_avg: partial.status === "included",
    reason: null,
    override_mode: null,
    high_bar: 2.5,
    similar_tomorrow_passes: true,
    ...partial,
  };
}

describe("groupUsageDayAudit", () => {
  it("groups by date with included/excluded split", () => {
    const groups = groupUsageDayAudit([
      row({ item: "Açaí", submitted_date: "2026-07-25", status: "included", delta: -2 }),
      row({ item: "Mango", submitted_date: "2026-07-25", status: "excluded", reason: "zero usage", delta: 0 }),
      row({ item: "Açaí", submitted_date: "2026-07-24", status: "excluded", reason: "restock", delta: 15 }),
    ]);
    expect(groups).toHaveLength(2);
    expect(groups[0].date).toBe("2026-07-25");
    expect(groups[0].included.map((r) => r.item)).toEqual(["Açaí"]);
    expect(groups[0].excluded.map((r) => r.item)).toEqual(["Mango"]);
  });
});

describe("pivotUsageDayAudit", () => {
  it("builds date × base matrix with qty cells", () => {
    const matrix = pivotUsageDayAudit([
      row({ item: "Mango", submitted_date: "2026-07-25", status: "excluded", reason: "zero usage", qty: 3 }),
      row({ item: "Açaí", submitted_date: "2026-07-25", status: "included", qty: 8 }),
      row({ item: "Açaí", submitted_date: "2026-07-24", status: "included", qty: 10 }),
    ]);
    expect(matrix.dates).toEqual(["2026-07-25", "2026-07-24"]);
    expect(matrix.bases).toEqual(["Açaí", "Mango"]);
    expect(matrix.cells.get(cellKey("2026-07-25", "Açaí"))?.qty).toBe(8);
    expect(matrix.cells.get(cellKey("2026-07-25", "Mango"))?.reason).toBe("zero usage");
    expect(matrix.cells.get(cellKey("2026-07-24", "Mango"))).toBeUndefined();
  });
});

describe("formatQty / formatDelta / statusTag / previewLine", () => {
  it("formats qty and signed deltas", () => {
    expect(formatQty(8)).toBe("8.0");
    expect(formatQty(null)).toBe("—");
    expect(formatDelta(-2)).toBe("-2.0");
    expect(formatDelta(1.5)).toBe("+1.5");
  });

  it("status tags prefer short reason / override", () => {
    expect(statusTag(row({ item: "A", submitted_date: "2026-07-25", status: "included" }))).toBe(
      "in avg",
    );
    expect(
      statusTag(
        row({
          item: "A",
          submitted_date: "2026-07-25",
          status: "excluded",
          reason: "zero usage",
        }),
      ),
    ).toBe("zero usage");
    expect(
      statusTag(
        row({
          item: "A",
          submitted_date: "2026-07-25",
          status: "excluded",
          override_mode: "force_exclude",
          reason: "force_exclude",
        }),
      ),
    ).toBe("force out");
  });

  it("builds threshold preview copy", () => {
    expect(
      previewLine({
        highBarBefore: 2.41,
        highBarAfter: 2.41,
        similarPasses: false,
        delta: 4,
      }),
    ).toContain("still excluded");
  });
});
