import { describe, expect, it } from "vitest";
import {
  applyPayCycleWording,
  cadenceSummary,
  composeMessage,
  formatLeaderboard,
  formatPayCycleLabel,
  parseDays,
  timeOfDayGreeting,
} from "@/lib/automations/teamPulse";

function shouldRun(days: number[], weekday: number, enabled: boolean): boolean {
  return enabled && days.includes(weekday);
}

describe("teamPulse compose", () => {
  it("fills leaderboard placeholder", () => {
    const out = composeMessage("A\n\n{leaderboard}\n\nB", "*   **Ada** leading with $10.");
    expect(out).toContain("**Ada**");
    expect(out).not.toContain("{leaderboard}");
  });

  it("groups equal bonuses", () => {
    const md = formatLeaderboard([
      { employee: "Willingham, Brooke", total_bonus: 70 },
      { employee: "Priyosha, Jarin", total_bonus: 70 },
      { employee: "Garcia, Jacob", total_bonus: 6.67 },
    ]);
    expect(md).toContain("Brooke Willingham");
    expect(md).toContain("Jarin Priyosha");
    expect(md).toContain("$70");
    expect(md).toContain("Jacob Garcia");
    expect(md).toContain("at $6.67");
  });

  it("parses days and cadence", () => {
    expect(parseDays("[1,3,6]")).toEqual([1, 3, 6]);
    expect(cadenceSummary([1, 3, 6], 8, 0, "America/Chicago")).toBe(
      "Tue · Thu · Sun · 08:00 America/Chicago",
    );
  });

  it("day gate", () => {
    expect(shouldRun([1, 3, 6], 1, true)).toBe(true);
    expect(shouldRun([1, 3, 6], 0, true)).toBe(false);
    expect(shouldRun([1, 3, 6], 1, false)).toBe(false);
  });

  it("pay-cycle label is current vs dated (Issue #245)", () => {
    expect(
      formatPayCycleLabel({
        periodStart: "2026-08-10",
        periodEnd: "2026-08-23",
        isCurrent: true,
      }),
    ).toBe("current pay cycle");
    expect(
      formatPayCycleLabel({
        periodStart: "2026-07-27",
        periodEnd: "2026-08-09",
        isCurrent: false,
      }),
    ).toBe("the Jul 27 – Aug 9 pay cycle");
  });

  it("rewrites legacy current wording for prior periods", () => {
    const legacy =
      "Sharing current pay cycle's leaderboard based of Google Review Bonus.";
    const out = applyPayCycleWording(legacy, {
      periodStart: "2026-07-27",
      periodEnd: "2026-08-09",
      isCurrent: false,
    });
    expect(out).toContain("the Jul 27 – Aug 9 pay cycle's leaderboard");
    expect(out).not.toMatch(/current pay cycle/i);
  });

  it("fills {pay_cycle} placeholder in compose", () => {
    const out = composeMessage(
      "Sharing {pay_cycle}'s leaderboard.\n\n{leaderboard}",
      "*   **Ada** leading with $10.",
      {
        periodStart: "2026-07-27",
        periodEnd: "2026-08-09",
        isCurrent: false,
      },
    );
    expect(out).toContain("the Jul 27 – Aug 9 pay cycle's leaderboard");
    expect(out).toContain("**Ada**");
    expect(out).not.toContain("{pay_cycle}");
  });

  it("greeting follows Chicago time of day", () => {
    // 2026-08-11 15:00 UTC = 10:00 CT → Morning
    const morning = new Date("2026-08-11T15:00:00Z");
    expect(timeOfDayGreeting(morning)).toBe("Good Morning");
    // 18:00 UTC = 13:00 CT → Afternoon
    const afternoon = new Date("2026-08-11T18:00:00Z");
    expect(timeOfDayGreeting(afternoon)).toBe("Good Afternoon");
    // 01:00 UTC next day = 20:00 CT → Evening
    const evening = new Date("2026-08-12T01:00:00Z");
    expect(timeOfDayGreeting(evening)).toBe("Good Evening");

    const out = composeMessage(
      "Good Morning Team ! Sharing {pay_cycle}'s board.\n\n{leaderboard}",
      "*   **Ada** leading with $10.",
      { periodStart: "2026-08-10", periodEnd: "2026-08-23", isCurrent: true },
      afternoon,
    );
    expect(out.startsWith("Good Afternoon Team")).toBe(true);
    expect(out).not.toMatch(/Good Morning/i);
  });
});
