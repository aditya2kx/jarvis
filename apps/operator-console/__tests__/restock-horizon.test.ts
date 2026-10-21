import { describe, expect, it } from "vitest";
import { planHorizonAdditions, parseHorizon } from "@/lib/inventory/restockHorizon";

const plan = (o: Partial<Parameters<typeof planHorizonAdditions>[0]> = {}) =>
  planHorizonAdditions({ todayIso: "2026-10-19", weekday: "friday", horizon: 2, scheduled: [], ...o });

describe("planHorizonAdditions", () => {
  it("returns empty when weekday unset or unknown, or horizon < 1", () => {
    expect(plan({ weekday: null })).toEqual([]);
    expect(plan({ weekday: "funday" })).toEqual([]);
    expect(plan({ horizon: 0 })).toEqual([]);
  });

  it("fills two Fridays from empty (Mon 2026-10-19)", () => {
    expect(plan()).toEqual(["2026-10-23", "2026-10-30"]);
  });

  it("tops up one when one Friday is live", () => {
    expect(plan({ weekday: "Friday", scheduled: ["2026-10-23"] })).toEqual(["2026-10-30"]);
  });

  it("no-ops when already two", () => {
    expect(plan({ scheduled: ["2026-10-23", "2026-10-30"] })).toEqual([]);
  });

  it("ignores past scheduled dates", () => {
    expect(plan({ scheduled: ["2026-10-16"] })).toEqual(["2026-10-23", "2026-10-30"]);
  });

  it("skips a week that already has a Thursday exception", () => {
    expect(plan({ scheduled: ["2026-10-22"] })).toEqual(["2026-10-30"]);
  });

  it("includes today when today is the delivery weekday", () => {
    expect(plan({ todayIso: "2026-10-23" })).toEqual(["2026-10-23", "2026-10-30"]);
  });

  it("after today's delivery-day closing, adds the week after next", () => {
    expect(
      plan({ todayIso: "2026-10-23", todayClosed: true, scheduled: ["2026-10-23", "2026-10-30"] }),
    ).toEqual(["2026-11-06"]);
  });

  it("never re-adds an operator-removed date", () => {
    expect(plan({ scheduled: ["2026-10-23"], removed: ["2026-10-30"] })).toEqual(["2026-11-06"]);
  });
});

describe("parseHorizon", () => {
  it("defaults to 2", () => {
    expect(parseHorizon(null)).toBe(2);
    expect(parseHorizon("")).toBe(2);
    expect(parseHorizon("x")).toBe(2);
  });
  it("parses integers", () => {
    expect(parseHorizon("3")).toBe(3);
  });
});
