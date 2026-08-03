import { describe, expect, it } from "vitest";
import {
  concurrentFromRanges,
  parseShiftRange,
  parseShiftRangesJson,
} from "@/lib/labor/shift-ranges";
import {
  aggregateScheduledDays,
  rollConcurrentToGrain,
} from "@/lib/labor/schedule-aggregate";

describe("parseShiftRange", () => {
  it("parses ADP range strings", () => {
    expect(parseShiftRange("1:30 PM - 8:30 PM")).toEqual({
      startMin: 13 * 60 + 30,
      endMin: 20 * 60 + 30,
      hours: 7,
    });
  });
});

describe("concurrentFromRanges", () => {
  it("divides hours by first-start → last-end span", () => {
    const ranges = parseShiftRangesJson('["10:00 AM - 2:00 PM", "12:00 PM - 6:00 PM"]');
    // span 10:00–18:00 = 8h; hours 4+6=10 → 1.25
    expect(concurrentFromRanges(10, ranges)).toBe(1.25);
  });
});

describe("aggregateScheduledDays", () => {
  it("buckets PT/FT and concurrent", () => {
    const days = aggregateScheduledDays([
      {
        date: "2026-08-03",
        labor_bucket: "parttime",
        scheduled_hours: 7,
        shift_ranges_json: '["1:30 PM - 8:30 PM"]',
      },
      {
        date: "2026-08-03",
        labor_bucket: "fulltime",
        scheduled_hours: 8,
        shift_ranges_json: '["9:00 AM - 5:00 PM"]',
      },
    ]);
    expect(days).toHaveLength(1);
    expect(days[0]!.parttime_hours).toBe(7);
    expect(days[0]!.fulltime_hours).toBe(8);
    expect(days[0]!.parttime_concurrent).toBe(1);
    expect(days[0]!.fulltime_concurrent).toBe(1);
  });

  it("concurrent uses wall-clock ranges, not inflated paid hours", () => {
    // Sparse scrape wrongly stored 20 paid hours on an 8.5h wall shift.
    const days = aggregateScheduledDays([
      {
        date: "2026-08-10",
        labor_bucket: "fulltime",
        scheduled_hours: 20,
        shift_ranges_json: '["8:00 AM - 4:30 PM"]',
      },
    ]);
    expect(days[0]!.fulltime_hours).toBe(20);
    expect(days[0]!.fulltime_concurrent).toBe(1);
  });

  it("rolls concurrent to week as average of days", () => {
    const days = aggregateScheduledDays([
      {
        date: "2026-08-03",
        labor_bucket: "parttime",
        scheduled_hours: 7,
        shift_ranges_json: '["1:30 PM - 8:30 PM"]',
      },
      {
        date: "2026-08-04",
        labor_bucket: "parttime",
        scheduled_hours: 7,
        shift_ranges_json: '["1:30 PM - 8:30 PM"]',
      },
    ]);
    const rolled = rollConcurrentToGrain(days, "week");
    expect(rolled).toHaveLength(1);
    expect(rolled[0]!.parttime_concurrent).toBe(1);
  });
});
