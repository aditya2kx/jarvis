import { describe, expect, it } from "vitest";
import { assignRowKey, summarizeAssign, validateAssignItems, type AssignRow } from "@/lib/labor/open-shift-assign";

const TODAY = "2026-10-19";
const ITEM = { date: "2026-10-27", startMin: 990, endMin: 1230, employee: "Denton, Cimino R" };

describe("validateAssignItems", () => {
  it("accepts future open shifts and trims names", () => {
    expect(validateAssignItems([{ ...ITEM, employee: "  Denton, Cimino R " }], TODAY)).toEqual([ITEM]);
  });

  it.each([
    [[], /No open shifts selected/],
    [[{ ...ITEM, date: TODAY }], /today or in the past/],
    [[{ ...ITEM, date: "10/27/2026" }], /bad date/],
    [[{ ...ITEM, endMin: 990 }], /bad start\/end/],
    [[{ ...ITEM, employee: " " }], /choose who takes it/],
    [[ITEM, { ...ITEM, startMin: 600, endMin: 900 }], /already takes another selected shift/],
    [Array.from({ length: 41 }, () => ITEM), /max 40/],
  ])("rejects %#", (items, msg) => {
    expect(() => validateAssignItems(items, TODAY)).toThrow(msg);
  });
});

describe("assignRowKey / summarizeAssign", () => {
  it("keys a row by store, shift and person", () => {
    expect(assignRowKey("palmetto", ITEM)).toBe("palmetto|2026-10-27|990|1230|Denton, Cimino R");
  });

  it("counts outcomes and words failures", () => {
    const row = (status: AssignRow["status"], error: string | null = null): AssignRow => ({
      assign_id: "a1",
      row_key: "k",
      date: ITEM.date,
      employee: ITEM.employee,
      start_min: ITEM.startMin,
      end_min: ITEM.endMin,
      status,
      error,
      adp_name: null,
      updated_at: null,
    });
    const s = summarizeAssign([row("assigned"), row("skipped"), row("failed", "not eligible"), row("queued")]);
    expect(s).toMatchObject({ assigned: 1, skipped: 1, failed: 1, queued: 1 });
    expect(s.errors).toEqual(["Denton, Cimino R 2026-10-27: not eligible"]);
  });
});
