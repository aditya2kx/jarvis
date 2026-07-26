import { describe, expect, it } from "vitest";
import {
  cellKey,
  computeLowHighBars,
  formatDelta,
  formatQty,
  formatThresholdImpact,
  groupUsageDayAudit,
  matrixStatusTag,
  pivotUsageDayAudit,
  previewLine,
  statusTag,
  thresholdImpactForDraft,
  usageUnitsFromDelta,
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

  it("matrix tags stay calm; drawer tags may name override", () => {
    expect(matrixStatusTag(row({ item: "A", submitted_date: "2026-07-25", status: "included" }))).toBe(
      "in avg",
    );
    expect(
      matrixStatusTag(
        row({
          item: "A",
          submitted_date: "2026-07-25",
          status: "excluded",
          reason: "zero usage",
        }),
      ),
    ).toBe("zero usage");
    expect(
      matrixStatusTag(
        row({
          item: "A",
          submitted_date: "2026-07-25",
          status: "excluded",
          override_mode: "force_exclude",
          reason: "force_exclude",
        }),
      ),
    ).toBe("excluded");
    expect(
      matrixStatusTag(
        row({
          item: "A",
          submitted_date: "2026-07-25",
          status: "included",
          override_mode: "force_include",
          reason: "included",
        }),
      ),
    ).toBe("in avg");
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
    ).toBe("force exclude");
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

  it("computeLowHighBars mirrors 20% low and median+2.5·MAD high", () => {
    const bars = computeLowHighBars([1, 2, 2, 2, 10]);
    expect(bars.medianNonzero).toBe(2);
    expect(bars.low).toBeCloseTo(0.4, 5);
    // survivors exclude low (<0.4) and keep 1,2,2,2,10 — med 2, mad 0 → high null when mad=0
    // with mad=0 SQL sets high_bar NULL; we match
    expect(bars.high).toBeNull();
  });

  it("force_include preview shows joining pool and bar shift", () => {
    const pool = [
      row({ item: "Açaí", submitted_date: "2026-07-20", status: "included", in_avg: true, delta: -2 }),
      row({ item: "Açaí", submitted_date: "2026-07-21", status: "included", in_avg: true, delta: -2 }),
      row({
        item: "Açaí",
        submitted_date: "2026-07-25",
        status: "excluded",
        in_avg: false,
        reason: "high outlier",
        delta: -5,
        high_bar: 3,
      }),
    ];
    expect(usageUnitsFromDelta(-5)).toBe(5);
    const impact = thresholdImpactForDraft(pool, "2026-07-25", "force_include");
    expect(impact.inPoolBefore).toBe(false);
    expect(impact.inPoolAfter).toBe(true);
    expect(impact.usage).toBe(5);
    expect(impact.lowAfter).not.toBeNull();
    const text = formatThresholdImpact(impact);
    expect(text).toContain("Will add to the average");
    expect(text).toContain("Used ~5 tubs");
    expect(text).toContain("Typical day:");
    expect(text).toContain("Similar day tomorrow:");
    expect(text).not.toContain("MAD");
    expect(text).not.toContain("avg pool");
  });
});
