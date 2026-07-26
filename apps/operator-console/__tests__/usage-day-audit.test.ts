import { describe, expect, it } from "vitest";
import {
  formatDelta,
  groupUsageDayAudit,
  previewLine,
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

describe("formatDelta / previewLine", () => {
  it("formats signed deltas", () => {
    expect(formatDelta(-2)).toBe("-2.0");
    expect(formatDelta(1.5)).toBe("+1.5");
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
