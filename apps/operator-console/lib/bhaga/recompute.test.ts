import { describe, expect, it } from "vitest";
import { pickRecomputeAnchorDate } from "@/lib/bhaga/recomputeAnchor";

describe("pickRecomputeAnchorDate", () => {
  it("returns null for empty input", () => {
    expect(pickRecomputeAnchorDate([])).toBeNull();
    expect(pickRecomputeAnchorDate(["", ""])).toBeNull();
  });

  it("dedupes and picks the latest date as the single job anchor", () => {
    expect(
      pickRecomputeAnchorDate(["2026-07-06", "2026-07-09", "2026-07-06", "2026-07-08"]),
    ).toBe("2026-07-09");
  });

  it("handles a single date", () => {
    expect(pickRecomputeAnchorDate(["2026-07-06"])).toBe("2026-07-06");
  });
});
