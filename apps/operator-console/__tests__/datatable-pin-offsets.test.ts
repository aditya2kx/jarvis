import { describe, expect, it } from "vitest";
import {
  accumulateDomPinOffsets,
  computeMetaPinOffsets,
  pinOffsetsEqual,
} from "@/lib/tables/pin-offsets";

describe("computeMetaPinOffsets", () => {
  const cols = [
    { accessorKey: "date", meta: { width: 88 } },
    { accessorKey: "from_account", meta: { width: 140 } },
    { accessorKey: "to_account", meta: { width: 140 } },
    { accessorKey: "spend", meta: { width: 100 } },
    { accessorKey: "earned", meta: { width: 100 } },
    { accessorKey: "transaction_name", meta: { width: 320 } },
  ];

  it("returns cumulative lefts for Accounting pinLeft", () => {
    expect(
      computeMetaPinOffsets(cols, [
        "date",
        "from_account",
        "to_account",
        "spend",
        "earned",
      ]),
    ).toEqual({
      date: 0,
      from_account: 88,
      to_account: 228,
      spend: 368,
      earned: 468,
    });
  });

  it("returns null when any pinned col lacks width (forces DOM path)", () => {
    expect(
      computeMetaPinOffsets(
        [{ accessorKey: "Item" }, { accessorKey: "Qty", meta: { width: 80 } }],
        ["Item", "Qty"],
      ),
    ).toBeNull();
  });

  it("returns null for empty pinLeft", () => {
    expect(computeMetaPinOffsets(cols, [])).toBeNull();
  });

  it("matches by column id when accessorKey absent", () => {
    expect(
      computeMetaPinOffsets([{ id: "Base", meta: { width: 120 } }], ["Base"]),
    ).toEqual({ Base: 0 });
  });
});

describe("pinOffsetsEqual", () => {
  it("true for same values even if different object identity", () => {
    expect(pinOffsetsEqual({ a: 0, b: 10 }, { a: 0, b: 10 })).toBe(true);
  });

  it("false when a value drifts (DOM measure jitter)", () => {
    expect(pinOffsetsEqual({ a: 0, b: 10 }, { a: 0, b: 11 })).toBe(false);
  });
});

describe("accumulateDomPinOffsets", () => {
  it("sums sequential header widths", () => {
    expect(
      accumulateDomPinOffsets([
        { colId: "Item", width: 120 },
        { colId: "Qty", width: 64 },
      ]),
    ).toEqual({ Item: 0, Qty: 120 });
  });
});
