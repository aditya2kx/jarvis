import { describe, expect, it } from "vitest";
import { GRAINS, bucketSql, formatBucket, parseGrain } from "@/lib/filters/range";

describe("parseGrain", () => {
  it("accepts day/week/month", () => {
    expect(parseGrain("day")).toBe("day");
    expect(parseGrain("week")).toBe("week");
    expect(parseGrain("month")).toBe("month");
  });

  it("falls back to the given default for an unknown/missing grain", () => {
    expect(parseGrain(undefined)).toBe("day");
    expect(parseGrain("bogus", "week")).toBe("week");
  });

  it("takes the first value when given an array", () => {
    expect(parseGrain(["month", "day"])).toBe("month");
  });
});

describe("bucketSql", () => {
  it("day is the raw column (no truncation)", () => {
    expect(bucketSql("day")).toBe("date");
  });

  it("week truncates to Monday-start ISO week", () => {
    expect(bucketSql("week")).toBe("DATE_TRUNC(date, WEEK(MONDAY))");
  });

  it("month truncates to calendar month", () => {
    expect(bucketSql("month")).toBe("DATE_TRUNC(date, MONTH)");
  });

  it("honors a custom date column name", () => {
    expect(bucketSql("week", "date_local")).toBe("DATE_TRUNC(date_local, WEEK(MONDAY))");
  });
});

describe("GRAINS", () => {
  it("has exactly the 3 operator-facing grains in display order", () => {
    expect(GRAINS.map((g) => g.value)).toEqual(["day", "week", "month"]);
  });
});

describe("formatBucket", () => {
  it("day renders as 'Jun 30'", () => {
    expect(formatBucket("2026-06-30", "day")).toBe("Jun 30");
  });

  it("week renders as 'Wk of <Monday>'", () => {
    expect(formatBucket("2026-06-29", "week")).toBe("Wk of Jun 29");
  });

  it("month renders as 'Jan 2026'", () => {
    expect(formatBucket("2026-01-01", "month")).toBe("Jan 2026");
  });

  it("returns an em dash for null/undefined/invalid", () => {
    expect(formatBucket(null, "day")).toBe("—");
    expect(formatBucket(undefined, "day")).toBe("—");
    expect(formatBucket("not-a-date", "day")).toBe("—");
  });
});
