import { describe, expect, it } from "vitest";
import { ACTIVE_BASES, buildSampleCsv, parseRestockCsv, validateParsedRows } from "@/lib/restock/parse";

// Mirrors handler.py's _parse_restock_csv test contract — same inputs, same
// outputs, so the app upload path and the Slack path stay identical.
describe("parseRestockCsv", () => {
  it("parses a CSV with a header row", () => {
    const { rows, errors } = parseRestockCsv("base,quantity\nAçaí,12\nCoconut,8");
    expect(errors).toEqual([]);
    expect(rows).toEqual([
      { item: "Açaí", quantityTubs: 12 },
      { item: "Coconut", quantityTubs: 8 },
    ]);
  });

  it("parses a CSV without a header row", () => {
    const { rows, errors } = parseRestockCsv("Açaí,12\nMango,5");
    expect(errors).toEqual([]);
    expect(rows).toEqual([
      { item: "Açaí", quantityTubs: 12 },
      { item: "Mango", quantityTubs: 5 },
    ]);
  });

  it("de-dups by base, last occurrence wins", () => {
    const { rows } = parseRestockCsv("Açaí,12\nAçaí,20");
    expect(rows).toEqual([{ item: "Açaí", quantityTubs: 20 }]);
  });

  it("rejects an unknown base", () => {
    const { rows, errors } = parseRestockCsv("Kale,5");
    expect(rows).toEqual([]);
    expect(errors[0]).toMatch(/unknown base/);
  });

  it("rejects a negative quantity", () => {
    const { errors } = parseRestockCsv("Açaí,-1");
    expect(errors[0]).toMatch(/must be >= 0/);
  });

  it("skips blank lines", () => {
    const { rows, errors } = parseRestockCsv("Açaí,12\n\nMango,5\n");
    expect(errors).toEqual([]);
    expect(rows).toHaveLength(2);
  });
});

describe("buildSampleCsv", () => {
  it("round-trips through parseRestockCsv with zero errors, one row per active base", () => {
    const { rows, errors } = parseRestockCsv(buildSampleCsv());
    expect(errors).toEqual([]);
    expect(rows).toHaveLength(ACTIVE_BASES.length);
    expect(rows.map((r) => r.item)).toEqual([...ACTIVE_BASES]);
    expect(rows.every((r) => r.quantityTubs === 0)).toBe(true);
  });
});

describe("validateParsedRows", () => {
  it("passes through valid candidates", () => {
    const { rows, errors } = validateParsedRows([{ item: "Açaí", quantity_tubs: 10, confidence: 0.9 }]);
    expect(errors).toEqual([]);
    expect(rows).toEqual([{ item: "Açaí", quantityTubs: 10 }]);
  });

  it("rejects an item outside the active bases", () => {
    const { rows, errors } = validateParsedRows([{ item: "Blueberry", quantity_tubs: 3 }]);
    expect(rows).toEqual([]);
    expect(errors[0]).toMatch(/unrecognized item/);
  });
});
