"use server";

import { revalidatePath } from "next/cache";
import { operatorEmail, DEFAULT_STORE } from "@/lib/auth/identity";
import { asAck, type ActionAck } from "@/lib/actions/types";
import { DEFAULT_WORKSPACE_ID, ensureDmChannel, postChatMessage } from "@/lib/automations/clickup";
import { insertAutomationPost } from "@/lib/bq/writes";
import { FEATURES } from "@/lib/config/features";
import { RECOGNITION } from "@/lib/config/stores";
import { reshapePost, writeBlurb } from "@/lib/recognition/blurbs";
import { draftDm, recapDm, type RecapLine } from "@/lib/recognition/draft";
import { sendEmail } from "@/lib/recognition/gmail";
import {
  cardKey,
  issueForRecipient,
  MAX_CARDS,
  type IssueDeps,
  type Recipient,
  type RecipientResult,
} from "@/lib/recognition/issue";
import { isAwardMonth } from "@/lib/recognition/month";
import { activateGiftCard, createGiftCard, getGiftCard } from "@/lib/recognition/square";
import {
  giftCardByKey,
  insertPendingGiftCard,
  members as loadMembers,
  updateGiftCard,
} from "@/lib/recognition/store";
import { syncRecognitionSources, type SyncResult } from "@/lib/recognition/sync";
import { loadRecognitionView, spokenName } from "@/lib/recognition/view";

const PAGE = "/automations/monthly-recognition";

function cfg() {
  const c = RECOGNITION[DEFAULT_STORE];
  if (!c) throw new Error(`No recognition config for ${DEFAULT_STORE}`);
  return c;
}

function assertMonth(m: string): void {
  if (!isAwardMonth(m)) throw new Error("Invalid award month.");
}

function chicagoToday(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Chicago" }).format(new Date());
}

async function dmOperator(content: string): Promise<string> {
  const dm = await ensureDmChannel([cfg().operatorClickupUserId], DEFAULT_WORKSPACE_ID);
  return (await postChatMessage(dm.id, content, DEFAULT_WORKSPACE_ID)).id;
}

export async function syncRecognitionSourcesAction(
  trigger: "console" | "auto",
): Promise<ActionAck<SyncResult>> {
  return asAck(async () => {
    const by = await operatorEmail();
    const res = await syncRecognitionSources(DEFAULT_STORE, trigger, by);
    if (!res.ok) throw new Error(`Sync failed: ${res.error}`);
    revalidatePath(PAGE);
    return res;
  }, "ClickUp synced.");
}

/** Gemini-grounded blurbs for each winner (no side effects). */
export async function previewRecognitionAction(
  awardMonth: string,
): Promise<ActionAck<{ blurbs: Record<string, { text: string; source: "gemini" | "template" }> }>> {
  return asAck(async () => {
    assertMonth(awardMonth);
    const view = await loadRecognitionView(DEFAULT_STORE, awardMonth);
    const entries = await Promise.all(
      view.winners.map(async (w) => [
        w.key,
        await writeBlurb({ first: spokenName(w.resultName), award: w.award, awardMonth, why: w.why }),
      ] as const),
    );
    return { blurbs: Object.fromEntries(entries) };
  }, "Draft written.");
}

/** DMs the operator a short summary, then the post as its own message so it can be forwarded as-is. */
export async function dmRecognitionDraftAction(input: {
  awardMonth: string;
  post: string;
  giftCardSummary: string[];
}): Promise<ActionAck<{ message_id: string }>> {
  return asAck(async () => {
    assertMonth(input.awardMonth);
    if (!input.post.trim()) throw new Error("Draft is empty.");
    const by = await operatorEmail();
    await dmOperator(draftDm(input));
    const id = await dmOperator(input.post);
    await insertAutomationPost({
      store: DEFAULT_STORE,
      automation_id: `monthly-recognition-${input.awardMonth}`,
      post_date_ct: chicagoToday(),
      destination: "dm",
      channel_id: null,
      message_id: id,
      content: input.post,
      dry_run: false,
      trigger: "draft-dm",
      updated_by: by,
    });
    return { message_id: id };
  }, "Draft sent to your ClickUp DM — forward the second message to #running-austin-palmetto.");
}

/** Gemini rewrite of the whole post per the operator's instruction (no side effects). */
export async function reshapeRecognitionPostAction(input: {
  awardMonth: string;
  post: string;
  instruction: string;
}): Promise<ActionAck<{ post: string }>> {
  return asAck(async () => {
    assertMonth(input.awardMonth);
    if (!input.instruction.trim()) throw new Error("Type how you want the message changed.");
    if (input.instruction.length > 1000) throw new Error("Instruction is too long.");
    const view = await loadRecognitionView(DEFAULT_STORE, input.awardMonth);
    const post = await reshapePost({
      post: input.post,
      instruction: input.instruction,
      awardMonth: input.awardMonth,
      winners: view.winners.map((w) => ({ first: spokenName(w.resultName), award: w.award, why: w.why })),
    });
    return { post };
  }, "Message reshaped.");
}

/** Record cards the operator already bought and sent by hand, so nothing is issued twice. */
export async function markGiftCardsIssuedAction(input: {
  awardMonth: string;
  recipients: ApproveRecipient[];
}): Promise<ActionAck<{ marked: number }>> {
  return asAck(async () => {
    assertMonth(input.awardMonth);
    const by = await operatorEmail();
    let marked = 0;
    for (const r of input.recipients) {
      if (!r.clickupUserId) throw new Error(`Pick the ClickUp member for ${r.displayName} first.`);
      const cards = Math.max(1, Math.min(MAX_CARDS, Math.trunc(r.cards)));
      for (let i = 1; i <= cards; i++) {
        const key = cardKey({ store: DEFAULT_STORE, awardMonth: input.awardMonth, clickupUserId: r.clickupUserId }, i);
        if (await giftCardByKey(key)) continue;
        await insertPendingGiftCard({
          store: DEFAULT_STORE,
          award_month: input.awardMonth,
          idempotency_key: key,
          clickup_user_id: r.clickupUserId,
          award: r.award,
          recipient_first: r.first.trim(),
          recipient_last: r.last.trim(),
          recipient_email: r.email.trim(),
          card_index: i,
          amount_cents: r.cardCents,
          location_id: cfg().squareLocationId,
          approved_by: by,
          status: "external",
        });
        marked += 1;
      }
    }
    revalidatePath(PAGE);
    return { marked };
  }, "Marked as already issued.");
}

export type ApproveRecipient = {
  clickupUserId: string;
  award: "MVP" | "High Five";
  displayName: string;
  first: string;
  last: string;
  email: string;
  cards: number;
  cardCents: number;
};

function issueDeps(): IssueDeps {
  return {
    byKey: giftCardByKey,
    insertPending: insertPendingGiftCard,
    update: updateGiftCard,
    create: createGiftCard,
    activate: activateGiftCard,
    get: getGiftCard,
    sendEmail,
    log: (l) => console.error(l),
  };
}

function toRecap(r: RecipientResult, displayName: string): RecapLine {
  return {
    displayName,
    award: r.recipient.award,
    email: r.recipient.email,
    cards: r.cards.map((c) => ({ last4: c.last4, amountCents: c.amountCents, status: c.status })),
    emailed: r.emailed,
    error: r.error,
  };
}

async function issueAll(
  awardMonth: string,
  recipients: (Omit<Recipient, "store" | "locationId" | "approvedBy" | "awardMonth"> & { displayName: string })[],
  resume: boolean,
): Promise<{ results: RecapLine[]; dmId: string | null }> {
  if (!FEATURES.recognitionGiftCards) {
    throw new Error("Gift cards are off (CONSOLE_RECOGNITION_GIFT_CARDS≠1) — no cards were created.");
  }
  const by = await operatorEmail();
  const results: RecapLine[] = [];
  for (const r of recipients) {
    const res = await issueForRecipient(
      { ...r, store: DEFAULT_STORE, awardMonth, locationId: cfg().squareLocationId, approvedBy: by },
      issueDeps(),
      { resume },
    );
    results.push(toRecap(res, r.displayName));
  }
  let dmId: string | null = null;
  try {
    dmId = await dmOperator(recapDm({ awardMonth, lines: results, isTest: awardMonth.startsWith("test-") }));
  } catch (e) {
    console.error(`[recognition] BREADCRUMB step=dm month=${awardMonth} err=${e instanceof Error ? e.message : e}`);
  }
  revalidatePath(PAGE);
  return { results, dmId };
}

export async function approveRecognitionAction(input: {
  awardMonth: string;
  recipients: ApproveRecipient[];
  resume?: boolean;
}): Promise<ActionAck<{ results: RecapLine[]; dmId: string | null }>> {
  return asAck(async () => {
    assertMonth(input.awardMonth);
    if (!input.recipients.length) throw new Error("No recipients.");
    const out = await issueAll(input.awardMonth, input.recipients, Boolean(input.resume));
    const failed = out.results.filter((r) => r.error);
    if (failed.length) {
      throw new Error(
        `${failed.length} recipient(s) need attention: ${failed.map((f) => `${f.displayName} — ${f.error}`).join("; ")}`,
      );
    }
    return out;
  }, "Gift cards issued and emailed. Recap sent to your DM.");
}

/** One small real card to the signed-in operator — proves Square + Gmail end to end. */
export async function sendTestGiftCardAction(
  cardCents: number,
): Promise<ActionAck<{ results: RecapLine[]; dmId: string | null }>> {
  return asAck(async () => {
    if (!Number.isInteger(cardCents) || cardCents < 100 || cardCents > 500) {
      throw new Error("Test card must be $1–$5.");
    }
    const email = (await operatorEmail()).trim().toLowerCase();
    const me = (await loadMembers(DEFAULT_WORKSPACE_ID)).find((m) => m.email?.toLowerCase() === email);
    if (!me) throw new Error(`No ClickUp member with email ${email} — sync first.`);
    const [first, ...rest] = me.username.split(/\s+/);
    const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "");
    const out = await issueAll(
      `test-${stamp}`,
      [{ clickupUserId: me.userId, award: "Test", displayName: me.username, first, last: rest.join(" "), email, cards: 1, cardCents }],
      false,
    );
    if (out.results[0]?.error) throw new Error(out.results[0].error);
    return out;
  }, "Test gift card created and emailed to you.");
}
