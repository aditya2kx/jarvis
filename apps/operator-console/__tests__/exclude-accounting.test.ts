import { describe, expect, it } from "vitest";
import {
  effectiveExclude,
  effectiveExcludeFromMap,
} from "@/lib/plaid/exclude-accounting";

describe("effectiveExclude", () => {
  it("inherits parent when leaf is null", () => {
    expect(
      effectiveExclude(
        { id: "sub", parent_id: "personal", exclude_from_accounting: null },
        { id: "personal", parent_id: null, exclude_from_accounting: true },
      ),
    ).toBe(true);
  });

  it("leaf false overrides parent true", () => {
    expect(
      effectiveExclude(
        { id: "sub", parent_id: "personal", exclude_from_accounting: false },
        { id: "personal", parent_id: null, exclude_from_accounting: true },
      ),
    ).toBe(false);
  });

  it("leaf true excludes regardless of parent", () => {
    expect(
      effectiveExclude(
        { id: "sub", parent_id: "ops", exclude_from_accounting: true },
        { id: "ops", parent_id: null, exclude_from_accounting: false },
      ),
    ).toBe(true);
  });

  it("missing leaf includes", () => {
    expect(effectiveExclude(null, null)).toBe(false);
  });

  it("resolves from flat map", () => {
    const nodes = [
      { id: "personal", parent_id: null, exclude_from_accounting: true },
      { id: "personal__toll", parent_id: "personal", exclude_from_accounting: null },
    ];
    expect(effectiveExcludeFromMap("personal__toll", nodes)).toBe(true);
    expect(effectiveExcludeFromMap("missing", nodes)).toBe(false);
  });
});
