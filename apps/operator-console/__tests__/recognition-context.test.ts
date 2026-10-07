import { describe, expect, it } from "vitest";

import { buildWhyPanel, messageUrl, type ChatRow, type Winner } from "@/lib/recognition/context";

// Synthetic content in the real #running / Shift Coverage / checklist / form formats.
const winner: Winner = {
  award: "MVP",
  resultName: "Carm",
  match: { name: "Carm", userId: "3", first: "Carmelita", last: "Nguyen", email: "c@example.com", how: "prefix" },
  rosterName: "Nguyen, Hillary",
  rosterHow: "last-name",
};

const row = (id: string, ch: string, at: string, user: string, content: string, parent: string | null = null): ChatRow => ({
  channel_id: ch,
  message_id: id,
  parent_message_id: parent,
  user_id: user,
  posted_at: at,
  content,
});

const base = {
  winner,
  memberNames: { "3": "Carmelita Nguyen", "9": "Lead Person" },
  windowStartIso: "2026-09-01T00:00:00Z",
  windowEndIso: "2026-10-05T00:00:00Z",
  shiftsInMonth: { days: 16, hours: 99.3, opening: 0, closing: 6, first_date: "2026-09-02", last_date: "2026-09-30" },
  shiftsAfterMonth: null,
};

describe("buildWhyPanel", () => {
  const nominationReplies = [
    row("n1", "rec", "2026-10-04T15:00:00Z", "9", "Carm for offering to close and having tons of referrals", "t9"),
    row("n2", "rec", "2026-10-02T15:00:00Z", "9", "Dario on picking bunch of closing shifts", "t9"),
    row("res", "rec", "2026-10-04T16:00:00Z", "9", "MVP → Carm\nHigh Five → Dario", "t9"),
  ];
  const auxMessages = [
    { ...row("a1", "run", "2026-09-28T20:00:00Z", "9", "Can [@Carmelita Nguyen](#user_mention#3) handle close without Jarin?"), role: "running" as const },
    { ...row("a2", "cov", "2026-09-22T15:00:00Z", "3", "I can take the 3pm close on Monday if nobody else can, just let me know"), role: "coverage" as const },
    { ...row("a3", "run", "2026-09-23T15:00:00Z", "3", "ok"), role: "running" as const },
    {
      ...row("a4", "run", "2026-09-24T03:00:00Z", "-1", "Checklist: 40/40\nShoutouts:\n[@Carmelita Nguyen](#user_mention#3) – 12\n[@Lead Person](#user_mention#9) – 3"),
      role: "running" as const,
    },
    { ...row("a5", "run", "2026-09-25T03:00:00Z", "-1", "Shift notes\nSubmitted by: Carmelita Nguyen |"), role: "running" as const },
    { ...row("a6", "run", "2026-08-15T15:00:00Z", "9", "Carm out of window"), role: "running" as const },
  ];

  const panel = buildWhyPanel({ ...base, nominationReplies, auxMessages, resultsMessageIds: ["res"] });

  it("keeps nominations naming the winner (by nickname), drops the results line", () => {
    expect(panel.items.filter((i) => i.source === "nomination").map((i) => i.text)).toEqual([
      "Carm for offering to close and having tons of referrals",
    ]);
  });

  it("collects mentions + substantive authored aux messages in the window", () => {
    const aux = panel.items.filter((i) => i.source !== "nomination");
    expect(aux.map((i) => i.source)).toEqual(["coverage", "running"]);
    expect(aux[1].text).toContain("@Carmelita Nguyen handle close");
    expect(aux[1].url).toBe("https://app.clickup.com/9017956545/chat/r/run/t/a1");
  });

  it("summarizes checklist shoutouts and shift-notes forms instead of listing them", () => {
    expect(panel.checklistShoutouts).toBe(1);
    expect(panel.checklistTasks).toBe(12);
    expect(panel.shiftNotes).toBe(1);
  });

  it("flags prefix and last-name-only matches", () => {
    expect(panel.flags.some((f) => f.includes("by prefix"))).toBe(true);
    expect(panel.flags.some((f) => f.includes("last name only"))).toBe(true);
  });

  it("flags shifts that fall after the award month", () => {
    const p = buildWhyPanel({
      ...base,
      shiftsInMonth: null,
      shiftsAfterMonth: { days: 3, hours: 22, opening: 0, closing: 3, first_date: "2026-10-01", last_date: "2026-10-04" },
      nominationReplies,
      auxMessages: [],
      resultsMessageIds: ["res"],
    });
    expect(p.flags.some((f) => f.includes("fall after it"))).toBe(true);
  });

  it("reply URLs point at the parent thread", () => {
    expect(messageUrl({ channel_id: "rec", message_id: "n1", parent_message_id: "t9" })).toBe(
      "https://app.clickup.com/9017956545/chat/r/rec/t/t9",
    );
  });
});
