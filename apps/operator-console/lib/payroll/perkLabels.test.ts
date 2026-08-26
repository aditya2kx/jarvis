import { describe, expect, it } from "vitest";
import { labelPerkId, parsePerkReasons } from "./perkLabels";

describe("parsePerkReasons", () => {
  it("parses id:dollars for multiple perks", () => {
    expect(parsePerkReasons("gym:20;phone:15")).toEqual([
      { id: "gym", label: "Gym", dollars: 20 },
      { id: "phone", label: "Phone", dollars: 15 },
    ]);
  });

  it("parses legacy perk_id-only agg", () => {
    expect(parsePerkReasons("gym")).toEqual([
      { id: "gym", label: "Gym", dollars: null },
    ]);
  });

  it("returns empty for blank", () => {
    expect(parsePerkReasons(null)).toEqual([]);
    expect(parsePerkReasons("")).toEqual([]);
  });
});

describe("labelPerkId", () => {
  it("title-cases snake ids", () => {
    expect(labelPerkId("phone_stipend")).toBe("Phone stipend");
  });

  it("uses named labels for reimbursements", () => {
    expect(labelPerkId("mileage")).toBe("Mileage");
    expect(labelPerkId("food_handler")).toBe("Food handler cert");
  });
});
