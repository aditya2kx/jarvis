import "server-only";

import type { WhyPanel } from "@/lib/recognition/context";
import { fallbackBlurb } from "@/lib/recognition/draft";
import { monthName } from "@/lib/recognition/month";
import type { Award } from "@/lib/recognition/parse";
import { acceptReshape } from "@/lib/recognition/reshape";

// Gemini-grounded copy (same direct-REST pattern as lib/automations/varyCopy.ts).
const GEMINI_URL =
  "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent";

const STYLE_EXAMPLE =
  "Jacob stepped up repeatedly throughout August, picking up extra shifts, covering mornings, and helping out when teammates needed coverage. We appreciate the ownership you’ve shown and how much we’ve been able to count on you to keep the shop running. A huge THANK YOU, Jacob! 🙌";

const NO_AUTOMATION_RULE =
  "Never mention checklist shout-outs, tasks completed, shift-notes forms, or anything posted by automations — only what people did and said.";

/** Reject model output that invents money, is multi-draft, or is too long. */
export function acceptBlurb(text: string): boolean {
  const t = text.trim();
  return t.length > 40 && t.length <= 700 && !t.includes("$") && !/^\s*(1\.|option|---)/im.test(t);
}

async function gemini(prompt: string, temperature: number): Promise<string | null> {
  const token = (process.env.GEMINI_TOKEN ?? "").trim();
  if (!token) return null;
  try {
    const res = await fetch(`${GEMINI_URL}?key=${token}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ contents: [{ parts: [{ text: prompt }] }], generationConfig: { temperature } }),
    });
    if (!res.ok) {
      console.warn(`[recognition] Gemini ${res.status}`);
      return null;
    }
    const body = (await res.json()) as { candidates?: { content?: { parts?: { text?: string }[] } }[] };
    return body.candidates?.[0]?.content?.parts?.[0]?.text?.trim() ?? null;
  } catch (e) {
    console.warn("[recognition] Gemini failed:", e);
    return null;
  }
}

/** People's messages only (automation posts carry author "Automation"), newest last. */
export function evidenceLines(why: WhyPanel, limit = 25): string {
  return why.items
    .filter((i) => i.author !== "Automation")
    .slice(-limit)
    .map((i) => `- [${i.source} ${i.at.slice(0, 10)} by ${i.author}] ${i.text.slice(0, 400)}`)
    .join("\n");
}

function shiftFacts(first: string, awardMonth: string, why: WhyPanel): string {
  const month = monthName(awardMonth);
  const s = why.shiftsInMonth;
  const after = why.shiftsAfterMonth;
  const facts = [
    s ? `${s.days} shift days, ${s.opening} opening and ${s.closing} closing shifts in ${month}` : null,
    after?.days ? `${after.days} shift days (${after.closing} closing) right after ${month}` : null,
  ].filter(Boolean);
  const timing =
    (s?.days ?? 0) === 0 && after?.days
      ? ` ${first} had no shifts in ${month}; say "recently" or "these past weeks", never "throughout ${month}".`
      : "";
  return `${facts.join("; ") || "(none)"}.${timing}`;
}

export async function writeBlurb(input: {
  first: string;
  award: Award;
  awardMonth: string;
  why: WhyPanel;
}): Promise<{ text: string; source: "gemini" | "template" }> {
  const nominations = input.why.items.filter((i) => i.source === "nomination").map((i) => i.text);
  const fallback = { text: fallbackBlurb(input.first, nominations), source: "template" as const };
  const month = monthName(input.awardMonth);
  const prompt =
    `Write the 2–3 sentence recognition blurb for ${input.first}, ${month} ${input.award} at a smoothie shop.\n` +
    `Match this tone and length exactly:\n"${STYLE_EXAMPLE}"\n\n` +
    `Rules: only use facts from the evidence below; prefer the nomination reasons; no dollar amounts; ` +
    `no other employees' names; end with a thank-you to ${input.first} and one emoji. ${NO_AUTOMATION_RULE} Return only the blurb.\n\n` +
    `Evidence:\n${evidenceLines(input.why) || "(none)"}\nShifts: ${shiftFacts(input.first, input.awardMonth, input.why)}`;
  const text = (await gemini(prompt, 0.6))?.replace(/^"|"$/g, "") ?? "";
  return acceptBlurb(text) ? { text, source: "gemini" } : fallback;
}

/** Rewrite the whole post per the operator's instruction, grounded in the same evidence. */
export async function reshapePost(input: {
  post: string;
  instruction: string;
  awardMonth: string;
  winners: { first: string; award: Award; why: WhyPanel }[];
}): Promise<string> {
  const evidence = input.winners
    .map(
      (w) =>
        `${w.first} (${w.award}):\n${evidenceLines(w.why, 12) || "(none)"}\nShifts: ${shiftFacts(w.first, input.awardMonth, w.why)}`,
    )
    .join("\n\n");
  const prompt =
    `You edit a ClickUp team-chat recognition post for a smoothie shop.\n` +
    `Operator instruction: ${input.instruction.trim()}\n\n` +
    `Rules:\n` +
    `1. Keep every mention token like [@Name](#user_mention#123) and [@followers](#task_user_group_mention#followers_tag) exactly as written.\n` +
    `2. Only add facts found in the evidence; never invent events, numbers, or names. ${NO_AUTOMATION_RULE}\n` +
    `3. Keep ClickUp markdown and emoji style. Return exactly one full post, nothing else.\n\n` +
    `CURRENT POST:\n${input.post}\n\nEVIDENCE:\n${evidence}`;
  const text = await gemini(prompt, 0.5);
  if (!text) throw new Error("Gemini unavailable (GEMINI_TOKEN missing or request failed).");
  const checked = acceptReshape(input.post, text);
  if (!checked.ok) throw new Error(`Gemini rewrite rejected: ${checked.reason}. Try again or rephrase.`);
  return checked.text;
}
