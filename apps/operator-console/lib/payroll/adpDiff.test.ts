import { describe, expect, it } from "vitest";
import { classifyAdpDiff } from "./adpDiff";

describe("classifyAdpDiff", () => {
  it("treats missing ADP earnings as not on check", () => {
    expect(classifyAdpDiff(null, 460.09)).toBe("not_on_check");
    expect(classifyAdpDiff(undefined, 70)).toBe("not_on_check");
  });

  it("treats near-zero as match when ADP paid a wage", () => {
    expect(classifyAdpDiff(372.41, -0.0)).toBe("match");
    expect(classifyAdpDiff(0, 0)).toBe("match");
  });

  it("flags a real variance when ADP has a row", () => {
    expect(classifyAdpDiff(100, 12.5)).toBe("variance");
  });
});
