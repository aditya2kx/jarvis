// Pure message composers for Monthly recognition (Issue #369): the team-channel
// post (style of the August 2026 #running-austin-palmetto recognition post), the
// gift-card email, and the operator DMs.

import type { Award } from "./parse";
import { monthName } from "./month";

export type DraftWinner = {
  award: Award;
  clickupUserId: string | null;
  displayName: string; // "Jacob Garcia"
  first: string;
  blurb: string; // 1–3 sentences on why
};

export type Prize = { bonusDollars: number; cards: number; cardCents: number };

/** Default prizes (May 2026 announcement): MVP $50 bonus, High Five $15; 2 × $15 gift cards each. */
export const DEFAULT_PRIZES: Record<Award, Prize> = {
  MVP: { bonusDollars: 50, cards: 2, cardCents: 1500 },
  "High Five": { bonusDollars: 15, cards: 2, cardCents: 1500 },
};

const AWARD_LINE: Record<Award, string> = { MVP: "🏆 MVP", "High Five": "🙌 High Five" };

export function mention(w: Pick<DraftWinner, "clickupUserId" | "displayName">): string {
  return w.clickupUserId ? `[@${w.displayName}](#user_mention#${w.clickupUserId})` : `@${w.displayName}`;
}

/** Fixed template; Gemini may only rewrite the per-winner blurbs. */
export function channelPost(input: {
  awardMonth: string;
  winners: DraftWinner[];
  giftCardsEmailed: boolean;
  leadMention: string | null;
}): string {
  const month = monthName(input.awardMonth);
  const n = input.winners.length;
  const count = n === 1 ? "one person" : n === 2 ? "two people" : `${n} people`;
  const ordered = [...input.winners].sort((a, b) => (a.award === b.award ? 0 : a.award === "MVP" ? -1 : 1));
  const blocks = ordered.map((w) => `${AWARD_LINE[w.award]} — ${mention(w)}\n${w.blurb.trim()}`);
  const prizeLine = input.giftCardsEmailed
    ? "Your bonuses have been added to payroll, and your Palmetto gift cards have been emailed to you to share with friends and family! 🎁"
    : `Your bonuses have been added to payroll — please reach out to ${input.leadMention ?? "your shift lead"} to collect your additional Palmetto gift cards to share with friends and family! 🎁`;
  const congrats = n === 2 ? "them both" : n === 1 ? "them" : "them all";
  return [
    `[@followers](#task_user_group_mention#followers_tag) 🍓 ${month} Recognition! 🎉`,
    "",
    `A big thank you to everyone who stepped up and supported each other throughout ${month}! This month, we’re recognizing ${count} who really showed what it means to be there for the team.`,
    "",
    blocks.join("\n\n"),
    "",
    prizeLine,
    "",
    `Please join us in congratulating ${congrats}! Thank you all for the effort you bring to our shop every day ❤️`,
  ].join("\n");
}

export function dollars(cents: number): string {
  return `$${(cents / 100).toFixed(cents % 100 === 0 ? 0 : 2)}`;
}

export type EmailCard = { gan: string; amountCents: number };

export function giftCardEmail(input: {
  first: string;
  award: Award | "Test";
  awardMonth: string;
  cards: EmailCard[];
}): { subject: string; text: string; html: string } {
  const isTest = input.award === "Test";
  const month = isTest ? "" : monthName(input.awardMonth);
  const subject = isTest
    ? "Palmetto gift card (test)"
    : `Your Palmetto ${month} ${input.award} gift cards 🎁`;
  const total = input.cards.reduce((s, c) => s + c.amountCents, 0);
  const intro = isTest
    ? `Hi ${input.first}, this is a test gift card from the Operator Console recognition flow.`
    : `Hi ${input.first}, congratulations on ${month} ${input.award}! Thank you for everything you did for the team. Here ${input.cards.length === 1 ? "is your Palmetto gift card" : `are your ${input.cards.length} Palmetto gift cards`} (${dollars(total)} total) to share with friends and family.`;
  const lines = input.cards.map((c, i) => `Card ${i + 1}: ${c.gan} — ${dollars(c.amountCents)}`);
  const how = "Show the card number at the register at Palmetto Superfoods Austin (Mueller), or forward it to someone you'd like to treat.";
  const text = [intro, "", ...lines, "", how, "", "— Palmetto Superfoods Austin"].join("\n");
  const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const html =
    `<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;font-size:15px;line-height:1.5;color:#1f2937">` +
    `<p>${esc(intro)}</p>` +
    `<table style="border-collapse:collapse;margin:12px 0">` +
    input.cards
      .map(
        (c, i) =>
          `<tr><td style="padding:6px 12px 6px 0;color:#6b7280">Card ${i + 1}</td>` +
          `<td style="padding:6px 12px;font-family:ui-monospace,Menlo,monospace;font-size:16px;letter-spacing:1px">${esc(c.gan)}</td>` +
          `<td style="padding:6px 0;font-weight:600">${dollars(c.amountCents)}</td></tr>`,
      )
      .join("") +
    `</table><p style="color:#4b5563">${esc(how)}</p><p>— Palmetto Superfoods Austin</p></div>`;
  return { subject, text, html };
}

export type RecapLine = {
  displayName: string;
  award: string;
  email: string | null;
  cards: { last4: string | null; amountCents: number; status: string }[];
  emailed: boolean;
  error: string | null;
};

export function recapDm(input: { awardMonth: string; lines: RecapLine[]; isTest: boolean }): string {
  const head = input.isTest
    ? "**Recognition gift cards — test run**"
    : `**${monthName(input.awardMonth)} recognition gift cards issued**`;
  const body = input.lines.map((l) => {
    const cards = l.cards
      .map((c) => `•••${c.last4 ?? "????"} ${dollars(c.amountCents)} (${c.status})`)
      .join(", ");
    const mail = l.emailed ? `emailed to ${l.email}` : `**not emailed**${l.error ? ` — ${l.error}` : ""}`;
    return `*   ${l.award} — ${l.displayName}: ${cards}; ${mail}`;
  });
  const total = input.lines.flatMap((l) => l.cards).reduce((s, c) => s + c.amountCents, 0);
  return [head, "", ...body, "", `Total loaded: ${dollars(total)}`].join("\n");
}

export function draftDm(input: { awardMonth: string; giftCardSummary: string[] }): string {
  return [
    `**${monthName(input.awardMonth)} recognition draft** — the next message is the post; forward it to #running-austin-palmetto.`,
    "",
    ...input.giftCardSummary.map((s) => `*   ${s}`),
  ].join("\n");
}

/** Template blurb from nomination reasons when Gemini is unavailable or rejected. */
export function fallbackBlurb(first: string, reasons: string[]): string {
  const cleaned = reasons
    .map((r) =>
      r
        .replace(/https?:\/\/\S+/g, "")
        .replace(new RegExp(`^@?${first}\\S*\\s+(for|on)\\s+`, "i"), "")
        .replace(/\s+/g, " ")
        .trim(),
    )
    .filter(Boolean)
    .slice(0, 2);
  if (!cleaned.length) return `Thank you for stepping up this month, ${first}! 🙌`;
  return `${first} was nominated for ${cleaned.join(", and ").replace(/[.!]+$/, "")}. Thank you for stepping up, ${first}! 🙌`;
}
