import { describe, expect, it } from "vitest";
import {
  GRAINS,
  WEEKDAY_ANCHOR_MON,
  addGrain,
  bucketSql,
  enumerateBucketStarts,
  formatBucket,
  parseGrain,
  truncateToGrain,
} from "@/lib/filters/range";

describe("parseGrain", () => {
  it("accepts day/week/month/weekday", () => {
    expect(parseGrain("day")).toBe("day");
    expect(parseGrain("week")).toBe("week");
    expect(parseGrain("month")).toBe("month");
    expect(parseGrain("weekday")).toBe("weekday");
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

  it("weekday maps DOW onto Mon 1970-01-05 … Sun anchors", () => {
    expect(bucketSql("weekday")).toBe(
      `DATE_ADD(DATE '${WEEKDAY_ANCHOR_MON}', INTERVAL MOD(EXTRACT(DAYOFWEEK FROM date) + 5, 7) DAY)`,
    );
  });

  it("honors a custom date column name", () => {
    expect(bucketSql("week", "date_local")).toBe("DATE_TRUNC(date_local, WEEK(MONDAY))");
  });
});

describe("GRAINS", () => {
  it("has the 4 operator-facing grains in display order", () => {
    expect(GRAINS.map((g) => g.value)).toEqual(["day", "week", "month", "weekday"]);
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

  it("weekday renders long DOW from anchor dates", () => {
    expect(formatBucket(WEEKDAY_ANCHOR_MON, "weekday")).toBe("Monday");
    expect(formatBucket("1970-01-11", "weekday")).toBe("Sunday");
    expect(formatBucket("2026-07-06", "weekday")).toBe("Monday"); // real Mon
  });

  it("returns an em dash for null/undefined/invalid", () => {
    expect(formatBucket(null, "day")).toBe("—");
    expect(formatBucket(undefined, "day")).toBe("—");
    expect(formatBucket("not-a-date", "day")).toBe("—");
  });

  it("day + weekday appends a compact two-letter DOW on a second line", () => {
    // 2026-06-30 is a Tuesday
    expect(formatBucket("2026-06-30", "day", { weekday: true })).toBe("Jun 30\nTu");
    // 2026-07-06 is a Monday
    expect(formatBucket("2026-07-06", "day", { weekday: true })).toBe("Jul 6\nMo");
  });

  it("weekday opt is ignored for week/month/weekday grains", () => {
    expect(formatBucket("2026-06-29", "week", { weekday: true })).toBe("Wk of Jun 29");
    expect(formatBucket("2026-01-01", "month", { weekday: true })).toBe("Jan 2026");
    expect(formatBucket(WEEKDAY_ANCHOR_MON, "weekday", { weekday: true })).toBe("Monday");
  });
});

describe("truncateToGrain / enumerate weekday", () => {
  it("maps calendar dates onto 1970 Mon…Sun anchors", () => {
    expect(truncateToGrain("2026-07-06", "weekday")).toBe(WEEKDAY_ANCHOR_MON); // Mon
    expect(truncateToGrain("2026-07-07", "weekday")).toBe("1970-01-06"); // Tue
    expect(truncateToGrain("2026-07-12", "weekday")).toBe("1970-01-11"); // Sun
  });

  it("enumerateBucketStarts always returns 7 weekday anchors", () => {
    const win = {
      start: "2026-07-01",
      end: "2026-07-15",
      label: "test",
      preset: "custom" as const,
    };
    expect(enumerateBucketStarts(win, "weekday")).toEqual([
      "1970-01-05",
      "1970-01-06",
      "1970-01-07",
      "1970-01-08",
      "1970-01-09",
      "1970-01-10",
      "1970-01-11",
    ]);
    expect(addGrain(WEEKDAY_ANCHOR_MON, "weekday", 1)).toBe("1970-01-06");
  });
});
