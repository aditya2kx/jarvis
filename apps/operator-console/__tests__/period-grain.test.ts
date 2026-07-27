import { beforeEach, describe, expect, it, vi } from "vitest";

const cookieStore = new Map<string, string>();

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => {
      const value = cookieStore.get(name);
      return value === undefined ? undefined : { name, value };
    },
  }),
}));

import { resolvePageGrain, resolvePageRange } from "@/lib/filters/period";

describe("resolvePageRange cookie fallback", () => {
  beforeEach(() => {
    cookieStore.clear();
  });

  it("URL range wins over cookie", async () => {
    cookieStore.set("oc_range", "7d");
    const win = await resolvePageRange("30d");
    expect(win.preset).toBe("30d");
  });

  it("uses non-custom cookie when URL range missing", async () => {
    cookieStore.set("oc_range", "last_week");
    const win = await resolvePageRange(undefined);
    expect(win.preset).toBe("last_week");
  });

  it("uses custom from/to cookies when range cookie is custom", async () => {
    cookieStore.set("oc_range", "custom");
    cookieStore.set("oc_from", "2026-06-01");
    cookieStore.set("oc_to", "2026-06-15");
    const win = await resolvePageRange(undefined);
    expect(win).toMatchObject({
      preset: "custom",
      start: "2026-06-01",
      end: "2026-06-15",
    });
  });

  it("falls back when custom cookie lacks valid from/to", async () => {
    cookieStore.set("oc_range", "custom");
    const win = await resolvePageRange(undefined);
    expect(win.preset).toBe("this_month");
  });
});

describe("resolvePageGrain cookie fallback", () => {
  beforeEach(() => {
    cookieStore.clear();
  });

  it("URL grain wins over cookie", async () => {
    cookieStore.set("oc_grain", "week");
    expect(await resolvePageGrain("month")).toBe("month");
  });

  it("uses grain cookie when URL missing", async () => {
    cookieStore.set("oc_grain", "week");
    expect(await resolvePageGrain(undefined)).toBe("week");
  });

  it("defaults to day when neither present", async () => {
    expect(await resolvePageGrain(undefined)).toBe("day");
  });
});
