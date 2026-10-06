import { describe, expect, it } from "vitest";

import { channelPost, fallbackBlurb, giftCardEmail, recapDm } from "@/lib/recognition/draft";

describe("channelPost", () => {
  const winners = [
    { award: "High Five" as const, clickupUserId: "2", displayName: "Brooklyn Reyes", first: "Brooklyn", blurb: "Brooklyn held the shop solo. Thank you! 💪" },
    { award: "MVP" as const, clickupUserId: "1", displayName: "Avery Stone", first: "Avery", blurb: "Avery covered mornings. A huge THANK YOU, Avery! 🙌" },
  ];

  it("matches the August post structure, MVP first, with mentions", () => {
    const post = channelPost({ awardMonth: "2026-09", winners, giftCardsEmailed: true, leadMention: null });
    expect(post.startsWith("[@followers](#task_user_group_mention#followers_tag) 🍓 September Recognition! 🎉")).toBe(true);
    expect(post).toContain("recognizing two people");
    expect(post.indexOf("🏆 MVP — [@Avery Stone](#user_mention#1)")).toBeLessThan(post.indexOf("🙌 High Five — [@Brooklyn Reyes](#user_mention#2)"));
    expect(post).toContain("gift cards have been emailed to you");
    expect(post).toContain("congratulating them both");
  });

  it("collect-from-lead line when cards are not emailed", () => {
    const post = channelPost({ awardMonth: "2026-09", winners, giftCardsEmailed: false, leadMention: "[@Lead](#user_mention#9)" });
    expect(post).toContain("please reach out to [@Lead](#user_mention#9) to collect");
  });
});

describe("giftCardEmail", () => {
  it("lists every card number and the total", () => {
    const m = giftCardEmail({ first: "Avery", award: "MVP", awardMonth: "2026-09", cards: [{ gan: "1111", amountCents: 1500 }, { gan: "2222", amountCents: 1500 }] });
    expect(m.subject).toBe("Your Palmetto September MVP gift cards 🎁");
    expect(m.text).toContain("Card 1: 1111 — $15");
    expect(m.text).toContain("($30 total)");
    expect(m.html).toContain("2222");
  });
});

describe("recapDm / fallbackBlurb", () => {
  it("recap shows last4 only", () => {
    const dm = recapDm({
      awardMonth: "2026-09",
      isTest: false,
      lines: [{ displayName: "Avery Stone", award: "MVP", email: "a@example.com", emailed: true, error: null, cards: [{ last4: "1000", amountCents: 1500, status: "emailed" }] }],
    });
    expect(dm).toContain("•••1000 $15 (emailed)");
    expect(dm).toContain("Total loaded: $15");
  });

  it("fallback blurb strips the leading name", () => {
    expect(fallbackBlurb("Carm", ["Carm for offering to close and having tons of referrals"])).toBe(
      "Carm was nominated for offering to close and having tons of referrals. Thank you for stepping up, Carm! 🙌",
    );
  });
});
