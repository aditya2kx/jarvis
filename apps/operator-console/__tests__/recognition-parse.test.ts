import { describe, expect, it } from "vitest";

import type { ChatRow } from "@/lib/recognition/context";
import { awardMonthForPeriod, monthBounds, recentAwardMonths } from "@/lib/recognition/month";
import {
  matchMember,
  matchRoster,
  monthOfThread,
  parseInlineAnnouncement,
  parseResults,
  type Member,
} from "@/lib/recognition/parse";
import { resolveMonth } from "@/lib/recognition/resolve";

// Formats copied from real #monthly-recognition messages; names are synthetic.
const members: Member[] = [
  { userId: "1", username: "Avery Stone", email: "avery@example.com" },
  { userId: "2", username: "Brooklyn Reyes", email: "brooklyn@example.com" },
  { userId: "3", username: "Carmelita Nguyen", email: "carm@example.com" },
  { userId: "4", username: "Dario Park", email: null },
  { userId: "5", username: "Dario Lee", email: "dlee@example.com" },
];

describe("monthOfThread", () => {
  it.each([
    ["🧵 September Nominations", "2026-09-07T15:00:00Z", "2026-09"],
    ["🧵to capture nominations for May", "2026-04-17T15:00:00Z", "2026-05"],
    ["🧵 October Recognition. FYI [@Avery Stone](#user_mention#1)", "2026-10-06T15:00:00Z", "2026-10"],
    ["🧵 January Nominations", "2026-12-28T15:00:00Z", "2027-01"],
  ])("%s → %s", (content, at, want) => {
    expect(monthOfThread(content, at)).toBe(want);
  });

  it("ignores reminders and chatter", () => {
    expect(
      monthOfThread(
        "Reminder to select Winners for Values of the Month and create new thread to track nominations",
        "2026-09-15T15:00:00Z",
      ),
    ).toBeNull();
    expect(monthOfThread("I am thinking of calling the bigger prize MVP", "2026-04-17T15:00:00Z")).toBeNull();
  });
});

describe("parseResults", () => {
  it("numbered name → award (August format)", () => {
    expect(parseResults("Results:\n1. Avery → MVP\n2. Brooklyn → High Five")).toEqual([
      { award: "MVP", names: ["Avery"], mentionIds: [] },
      { award: "High Five", names: ["Brooklyn"], mentionIds: [] },
    ]);
  });

  it("award → names with + (September format)", () => {
    expect(parseResults("MVP → Carm\nHigh Five → Dario + Brooklyn")).toEqual([
      { award: "MVP", names: ["Carm"], mentionIds: [] },
      { award: "High Five", names: ["Dario", "Brooklyn"], mentionIds: [] },
    ]);
  });

  it("bulleted mentions (June format)", () => {
    expect(
      parseResults(
        "*   High Five → [@Brooklyn Reyes](#user_mention#2)\n*   MVP → [@Avery Stone](#user_mention#1)",
      ),
    ).toEqual([
      { award: "High Five", names: ["Brooklyn Reyes"], mentionIds: ["2"] },
      { award: "MVP", names: ["Avery Stone"], mentionIds: ["1"] },
    ]);
  });

  it("ignores nomination replies", () => {
    expect(parseResults("Monthly Nomination from Brooklyn: Avery had a customer compliment")).toEqual([]);
    expect(parseResults("Carm for offering to close and having tons of referrals")).toEqual([]);
  });
});

describe("parseInlineAnnouncement (May weekly update format)", () => {
  it("attributes mentions to the preceding award heading", () => {
    const content =
      "Our crew members for May have been selected!\n**MVP** ($50 bonus) - [@Brooklyn Reyes](#user_mention#2) for deep clean\n" +
      "**High Five** ($15 bonus) [@Avery Stone](#user_mention#1) [@Dario Lee](#user_mention#5)\n" +
      "***Reminders*** [@Carmelita Nguyen](#user_mention#3)";
    expect(parseInlineAnnouncement(content)).toEqual([
      { award: "MVP", names: ["Brooklyn Reyes"], mentionIds: ["2"] },
      { award: "High Five", names: ["Avery Stone", "Dario Lee"], mentionIds: ["1", "5"] },
    ]);
  });
});

describe("matchMember", () => {
  it("mention id wins", () => {
    expect(matchMember("Avery Stone", "1", members)).toMatchObject({ userId: "1", how: "mention" });
  });
  it("unique first name → exact", () => {
    expect(matchMember("Brooklyn", null, members)).toMatchObject({
      userId: "2",
      first: "Brooklyn",
      last: "Reyes",
      email: "brooklyn@example.com",
      how: "exact",
    });
  });
  it("nickname prefix (Linh → Linhchi)", () => {
    expect(matchMember("Carm", null, members)).toMatchObject({ userId: "3", how: "prefix" });
  });
  it("duplicate first names are ambiguous", () => {
    expect(matchMember("Dario", null, members)).toMatchObject({ userId: null, how: "ambiguous" });
  });
  it("unknown → none", () => {
    expect(matchMember("Zed", null, members).how).toBe("none");
  });
});

describe("matchRoster", () => {
  const roster = ["Reyes, Brooklyn", "Nguyen, Hillary", "Stone, Avery J"];
  it("exact first+last", () => {
    expect(matchRoster("Avery", "Stone", roster)).toEqual({ canonical: "Stone, Avery J", how: "exact" });
  });
  it("last-name only when payroll uses a different first name", () => {
    expect(matchRoster("Carmelita", "Nguyen", roster)).toEqual({ canonical: "Nguyen, Hillary", how: "last-name" });
  });
  it("none", () => {
    expect(matchRoster("Zed", "Q", roster)).toEqual({ canonical: null, how: "none" });
  });
});

describe("month helpers", () => {
  it("award month is the month before period_end", () => {
    expect(awardMonthForPeriod("2026-10-04")).toBe("2026-09");
    expect(awardMonthForPeriod("2027-01-10")).toBe("2026-12");
  });
  it("bounds + recent list", () => {
    expect(monthBounds("2026-02")).toEqual({ start: "2026-02-01", end: "2026-02-28" });
    expect(recentAwardMonths("2026-01", 3)).toEqual(["2026-01", "2025-12", "2025-11"]);
  });
});

describe("resolveMonth", () => {
  const ch = "rec";
  const row = (id: string, at: string, content: string, parent: string | null = null): ChatRow => ({
    channel_id: ch,
    message_id: id,
    parent_message_id: parent,
    user_id: "9",
    posted_at: at,
    content,
  });
  const rows = [
    row("t8", "2026-07-31T15:00:00Z", "🧵 August Nominations"),
    row("t8r1", "2026-08-12T15:00:00Z", "Brooklyn for holding the shop alone", "t8"),
    row("t8res", "2026-09-08T15:00:00Z", "Results:\n1. Avery → MVP\n2. Brooklyn → High Five", "t8"),
    row("t9", "2026-09-07T15:00:00Z", "🧵 September Nominations"),
    row("t9r1", "2026-10-02T15:00:00Z", "Dario on picking bunch of closing shifts", "t9"),
    row("top9", "2026-10-04T15:00:00Z", "MVP → Carm\nHigh Five → Dario + Brooklyn"),
    row("t10", "2026-10-06T15:00:00Z", "🧵 October Recognition"),
  ];

  it("results from a thread reply", () => {
    const r = resolveMonth({ awardMonth: "2026-08", recognitionChannelId: ch, recognitionRows: rows, runningRows: [] });
    expect(r.thread?.message_id).toBe("t8");
    expect(r.resultsSource).toBe("thread-reply");
    expect(r.resultsMessage?.message_id).toBe("t8res");
    expect(r.replies.map((x) => x.message_id)).toEqual(["t8r1", "t8res"]);
  });

  it("falls back to a top-level results post after the thread", () => {
    const r = resolveMonth({ awardMonth: "2026-09", recognitionChannelId: ch, recognitionRows: rows, runningRows: [] });
    expect(r.resultsSource).toBe("channel");
    expect(r.results[1]).toMatchObject({ award: "High Five", names: ["Dario", "Brooklyn"] });
  });

  it("a top-level results post after the next thread opened still belongs to the prior month", () => {
    const noReply = rows.filter((r) => r.message_id !== "t8res");
    noReply.push(row("top8", "2026-09-08T15:00:00Z", "Results:\n1. Avery → MVP\n2. Brooklyn → High Five"));
    const aug = resolveMonth({ awardMonth: "2026-08", recognitionChannelId: ch, recognitionRows: noReply, runningRows: [] });
    expect(aug.resultsMessage?.message_id).toBe("top8");
    const sep = resolveMonth({ awardMonth: "2026-09", recognitionChannelId: ch, recognitionRows: noReply, runningRows: [] });
    expect(sep.resultsMessage?.message_id).toBe("top9");
  });

  it("no results yet → thread only", () => {
    const r = resolveMonth({ awardMonth: "2026-10", recognitionChannelId: ch, recognitionRows: rows, runningRows: [] });
    expect(r.thread?.message_id).toBe("t10");
    expect(r.results).toEqual([]);
  });
});
