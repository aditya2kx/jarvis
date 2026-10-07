import { describe, expect, it } from "vitest";

import { acceptReshape, mentionTokens, previewSegments } from "@/lib/recognition/reshape";

const avery = "[@Avery Stone](#user_mention#111)";
const blake = "[@Blake Rivera](#user_mention#222)";
const followers = "[@followers](#task_user_group_mention#followers_tag)";
const original = `${followers} 🎉 MVP: ${avery} — great month.\nHigh Five: ${blake} — covered shifts.`;

describe("acceptReshape", () => {
  it("accepts a rewrite that keeps every mention and strips code fences", () => {
    const rewrite = "```\n" + `${followers} Huge shout-out to ${avery} and ${blake} for an amazing September!` + "\n```";
    const r = acceptReshape(original, rewrite);
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.text.startsWith(followers)).toBe(true);
  });

  it("rejects a rewrite that drops a mention", () => {
    const r = acceptReshape(original, `${followers} Huge shout-out to ${avery} for an amazing September!`);
    expect(r).toEqual({ ok: false, reason: "dropped 1 @mention(s)" });
  });

  it("rejects multiple drafts and empty replies", () => {
    expect(acceptReshape(original, `Option 1: ${followers} ${avery} ${blake} nice work everyone`).ok).toBe(false);
    expect(acceptReshape(original, "ok").ok).toBe(false);
  });
});

describe("previewSegments", () => {
  it("renders mention markup as @Name and keeps the text around it", () => {
    const segs = previewSegments(`Hi ${avery}!`);
    expect(segs).toEqual([
      { text: "Hi ", mention: false },
      { text: "@Avery Stone", mention: true },
      { text: "!", mention: false },
    ]);
    expect(mentionTokens(original)).toHaveLength(3);
  });
});
