import { afterEach, describe, expect, it, vi } from "vitest";
import { RANGE_PRESETS, isMonthLike, resolveRange, wantsCustom } from "@/lib/filters/range";

// Fixed "now" for every test: Thu 2026-07-02, 13:00 Central (18:00Z) — safely
// mid-day so the America/Chicago "today" lookup can't straddle midnight.
const THURSDAY = "2026-07-02T18:00:00Z";

function withNow(iso: string, fn: () => void) {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(iso));
  try {
    fn();
  } finally {
    vi.useRealTimers();
  }
}

afterEach(() => {
  vi.useRealTimers();
});

describe("resolveRange — 7d/30d (rolling, end = today)", () => {
  it("7d is a 7-day window ending today", () => {
    withNow(THURSDAY, () => {
      expect(resolveRange("7d")).toEqual({
        start: "2026-06-26",
        end: "2026-07-02",
        label: "Last 7 days",
        preset: "7d",
      });
    });
  });

  it("30d is a 30-day window ending today", () => {
    withNow(THURSDAY, () => {
      expect(resolveRange("30d")).toEqual({
        start: "2026-06-03",
        end: "2026-07-02",
        label: "Last 30 days",
        preset: "30d",
      });
    });
  });
});

describe("resolveRange — this_week/last_week (Monday-start, ISO)", () => {
  it("this_week spans Monday..Sunday of the current week", () => {
    withNow(THURSDAY, () => {
      expect(resolveRange("this_week")).toEqual({
        start: "2026-06-29",
        end: "2026-07-05",
        label: "This week",
        preset: "this_week",
      });
    });
  });

  it("last_week is the full prior Monday..Sunday", () => {
    withNow(THURSDAY, () => {
      expect(resolveRange("last_week")).toEqual({
        start: "2026-06-22",
        end: "2026-06-28",
        label: "Last week",
        preset: "last_week",
      });
    });
  });

  it("Monday itself is the start of its own week (no off-by-one)", () => {
    withNow("2026-06-29T18:00:00Z", () => {
      expect(resolveRange("this_week").start).toBe("2026-06-29");
      expect(resolveRange("this_week").end).toBe("2026-07-05");
    });
  });

  it("Sunday itself is the end of its own week, not the start of the next", () => {
    withNow("2026-07-05T18:00:00Z", () => {
      expect(resolveRange("this_week").start).toBe("2026-06-29");
      expect(resolveRange("this_week").end).toBe("2026-07-05");
    });
  });
});

describe("resolveRange — this_month/last_month (calendar boundaries)", () => {
  it("this_month spans the 1st..last day of the current month", () => {
    withNow(THURSDAY, () => {
      expect(resolveRange("this_month")).toEqual({
        start: "2026-07-01",
        end: "2026-07-31",
        label: "This month",
        preset: "this_month",
      });
    });
  });

  it("last_month spans the full prior calendar month", () => {
    withNow(THURSDAY, () => {
      expect(resolveRange("last_month")).toEqual({
        start: "2026-06-01",
        end: "2026-06-30",
        label: "Last month",
        preset: "last_month",
      });
    });
  });

  it("last_month rolls back across a year boundary (Jan -> Dec of prior year)", () => {
    withNow("2026-01-15T18:00:00Z", () => {
      expect(resolveRange("last_month")).toEqual({
        start: "2025-12-01",
        end: "2025-12-31",
        label: "Last month",
        preset: "last_month",
      });
    });
  });
});

describe("resolveRange — DST-safety", () => {
  it("this_month is unaffected by the US spring-forward transition (Mar 2026)", () => {
    // DST starts Sun Mar 8, 2026 — pick a "today" after the transition and
    // confirm the month still resolves to the full 1..31 calendar range.
    withNow("2026-03-20T18:00:00Z", () => {
      expect(resolveRange("this_month")).toEqual({
        start: "2026-03-01",
        end: "2026-03-31",
        label: "This month",
        preset: "this_month",
      });
    });
  });

  it("this_week is unaffected by the US fall-back transition (Nov 2026)", () => {
    // DST ends Sun Nov 1, 2026 — pick a "today" the next day and confirm the
    // Monday-start week didn't shift by the DST hour change.
    withNow("2026-11-02T18:00:00Z", () => {
      expect(resolveRange("this_week")).toEqual({
        start: "2026-11-02",
        end: "2026-11-08",
        label: "This week",
        preset: "this_week",
      });
    });
  });
});

describe("resolveRange — fallback + input handling", () => {
  it("falls back to the given default for an unknown/missing preset", () => {
    withNow(THURSDAY, () => {
      expect(resolveRange(undefined, "7d").preset).toBe("7d");
      expect(resolveRange("bogus", "7d").preset).toBe("7d");
    });
  });

  it("defaults to 30d when no fallback is given", () => {
    withNow(THURSDAY, () => {
      expect(resolveRange(undefined).preset).toBe("30d");
    });
  });

  it("takes the first value when given an array (Next.js multi-value search param)", () => {
    withNow(THURSDAY, () => {
      expect(resolveRange(["this_week", "30d"]).preset).toBe("this_week");
    });
  });
});

describe("isMonthLike", () => {
  it("is true for 30d/this_month/last_month", () => {
    expect(isMonthLike("30d")).toBe(true);
    expect(isMonthLike("this_month")).toBe(true);
    expect(isMonthLike("last_month")).toBe(true);
  });

  it("is false for 7d/this_week/last_week", () => {
    expect(isMonthLike("7d")).toBe(false);
    expect(isMonthLike("this_week")).toBe(false);
    expect(isMonthLike("last_week")).toBe(false);
  });
});

describe("RANGE_PRESETS", () => {
  it("has exactly the 7 operator-facing presets in display order", () => {
    expect(RANGE_PRESETS.map((p) => p.value)).toEqual([
      "7d",
      "30d",
      "this_week",
      "this_month",
      "last_week",
      "last_month",
      "custom",
    ]);
  });
});

describe("resolveRange — custom", () => {
  it("uses the given from/to when both are valid and from <= to", () => {
    withNow(THURSDAY, () => {
      expect(resolveRange("custom", "30d", "2026-05-01", "2026-05-15")).toEqual({
        start: "2026-05-01",
        end: "2026-05-15",
        label: "Custom",
        preset: "custom",
      });
    });
  });

  it("accepts a single-day range (from === to)", () => {
    withNow(THURSDAY, () => {
      expect(resolveRange("custom", "30d", "2026-05-01", "2026-05-01").preset).toBe("custom");
    });
  });

  it("falls back when from is missing", () => {
    withNow(THURSDAY, () => {
      expect(resolveRange("custom", "7d", undefined, "2026-05-15").preset).toBe("7d");
    });
  });

  it("falls back when to is missing", () => {
    withNow(THURSDAY, () => {
      expect(resolveRange("custom", "7d", "2026-05-01", undefined).preset).toBe("7d");
    });
  });

  it("falls back when from is after to (inverted range)", () => {
    withNow(THURSDAY, () => {
      expect(resolveRange("custom", "7d", "2026-05-15", "2026-05-01").preset).toBe("7d");
    });
  });

  it("falls back when from/to are malformed", () => {
    withNow(THURSDAY, () => {
      expect(resolveRange("custom", "7d", "not-a-date", "2026-05-15").preset).toBe("7d");
    });
  });

  it("falls back to 30d if the fallback itself is custom (avoids infinite loop)", () => {
    withNow(THURSDAY, () => {
      expect(resolveRange("custom", "custom", undefined, undefined).preset).toBe("30d");
    });
  });

  it("takes the first value when from/to are arrays", () => {
    withNow(THURSDAY, () => {
      expect(resolveRange("custom", "30d", ["2026-05-01", "x"], ["2026-05-15", "y"])).toEqual({
        start: "2026-05-01",
        end: "2026-05-15",
        label: "Custom",
        preset: "custom",
      });
    });
  });
});

describe("wantsCustom", () => {
  it("is true as soon as range=custom is selected, even with no from/to yet", () => {
    // The exact scenario resolveRange's "falls back when to is missing" case
    // above covers from the data-fetch side — this is the UI-visibility side:
    // the DateRangePicker must still render so the operator can pick dates.
    expect(wantsCustom("custom")).toBe(true);
  });

  it("is false for any non-custom preset", () => {
    expect(wantsCustom("30d")).toBe(false);
    expect(wantsCustom("this_week")).toBe(false);
  });

  it("is false when the param is missing", () => {
    expect(wantsCustom(undefined)).toBe(false);
  });

  it("takes the first value when passed an array (mirrors resolveRange)", () => {
    expect(wantsCustom(["custom", "30d"])).toBe(true);
    expect(wantsCustom(["30d", "custom"])).toBe(false);
  });
});
