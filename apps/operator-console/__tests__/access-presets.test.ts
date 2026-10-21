import { describe, expect, it } from "vitest";
import { ACCESS_PRESETS, matchPreset } from "@/lib/access/presets";
import { NO_ACCESS, canPage, canSection, normalizeGrant, setPageGrant } from "@/lib/access/policy";
import { relativeTime } from "@/components/access/visuals";

const preset = (id: string) => ACCESS_PRESETS.find((p) => p.id === id)!.grant;

describe("access presets", () => {
  it("are already normalized, so saving one stores exactly what the drawer showed", () => {
    for (const p of ACCESS_PRESETS) expect(normalizeGrant(p.grant)).toEqual(p.grant);
  });

  it("store manager never sees money: no payroll, accounting or labor wages", () => {
    const g = preset("store-manager");
    expect(canPage(g, "payroll")).toBe(false);
    expect(canPage(g, "accounting")).toBe(false);
    expect(canSection(g, "labor", "labor-wages")).toBe(false);
    expect(canSection(g, "labor", "coverage")).toBe(true);
  });

  it("matches a grant to its preset and drops the match once customized", () => {
    expect(matchPreset(preset("shift-lead"))?.id).toBe("shift-lead");
    expect(matchPreset({ full: true, pages: {} })?.id).toBe("everything");
    expect(matchPreset(setPageGrant(preset("shift-lead"), "sales", "all"))).toBeNull();
    expect(matchPreset(NO_ACCESS)).toBeNull();
  });

  it("matches regardless of section order", () => {
    const shuffled = { full: false, pages: { inventory: "all" as const, labor: ["open-shifts", "coverage", "availability", "concurrent"], "order-quality": "all" as const } };
    expect(matchPreset(shuffled)?.id).toBe("shift-lead");
  });
});

describe("relativeTime", () => {
  const now = Date.parse("2026-10-21T12:00:00Z");
  it("reads naturally for recent and older changes", () => {
    expect(relativeTime("2026-10-21T11:59:30Z", now)).toBe("Just now");
    expect(relativeTime("2026-10-21T11:45:00Z", now)).toBe("15 min ago");
    expect(relativeTime("2026-10-21T09:00:00Z", now)).toBe("3 h ago");
    expect(relativeTime("2026-10-20T09:00:00Z", now)).toBe("Yesterday");
    expect(relativeTime("2026-10-17T12:00:00Z", now)).toBe("4 days ago");
    expect(relativeTime("2026-10-01T12:00:00Z", now)).toBe("Oct 1");
  });
});
