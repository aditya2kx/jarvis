import { describe, expect, it } from "vitest";
import { previewLine } from "./previewDiff";

describe("previewLine", () => {
  it("returns null when there is no snapshot", () => {
    expect(previewLine(10, null, "hours")).toBeNull();
  });

  it("hours within 30 minutes match", () => {
    const line = previewLine(458.97, 458.97, "hours");
    expect(line?.match).toBe(true);
    expect(line?.label).toContain("Matches last Preview");
  });

  it("hours delta uses minus vs last Preview", () => {
    const line = previewLine(10, 11.2, "hours");
    expect(line?.match).toBe(false);
    expect(line?.label).toBe("−1.20h vs last Preview (11.20h)");
  });

  it("total pay matches Preview Gross within $1", () => {
    const line = previewLine(8999.06, 8999.06, "pay");
    expect(line?.match).toBe(true);
  });

  it("total pay variance includes perks and bonuses in console total", () => {
    const line = previewLine(9005, 8999.06, "pay");
    expect(line?.match).toBe(false);
    expect(line?.label).toContain("vs last Preview");
  });
});
