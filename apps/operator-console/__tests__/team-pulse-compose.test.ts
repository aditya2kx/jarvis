import { describe, expect, it } from "vitest";
import {
  cadenceSummary,
  composeMessage,
  formatLeaderboard,
  parseDays,
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
});
