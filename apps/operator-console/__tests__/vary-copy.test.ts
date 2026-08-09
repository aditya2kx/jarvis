import { describe, expect, it } from "vitest";
import { acceptVariedCopy } from "@/lib/automations/varyAccept";

const LB =
  "*   **Alex Example** and **Sam Sample** leading with $40 each.\n" +
  "*   **Pat Placeholder** at $20.";

describe("acceptVariedCopy", () => {
  it("accepts a single rewrite with verbatim leaderboard", () => {
    const text =
      `Good morning, team!\n\n${LB}\n\nKeep up the great work.`;
    const out = acceptVariedCopy(text, LB);
    expect(out.varied).toBe(true);
    expect(out.text).toBe(text);
  });

  it("rejects multi-draft joined by ---", () => {
    const text =
      `Good morning, team!\n\n${LB}\n\nKeep going.\n\n---\n\n` +
      `Hi team!\n\n${LB}\n\nFantastic effort.\n\n---\n\n` +
      `Hey everyone!\n\n${LB}\n\nOne team.`;
    const out = acceptVariedCopy(text, LB);
    expect(out.varied).toBe(false);
    expect(out.text).toBe("");
  });

  it("rejects when leaderboard appears more than once", () => {
    const text = `A\n\n${LB}\n\nB\n\n${LB}\n\nC`;
    const out = acceptVariedCopy(text, LB);
    expect(out.varied).toBe(false);
  });

  it("rejects when leaderboard missing", () => {
    const out = acceptVariedCopy("Hello team, keep momentum!", LB);
    expect(out.varied).toBe(false);
  });

  it("rejects empty inputs", () => {
    expect(acceptVariedCopy("", LB).varied).toBe(false);
    expect(acceptVariedCopy("x", "").varied).toBe(false);
  });
});
