// Gift-card issuance state machine for Monthly recognition (Issue #369).
// Dependencies are injected so the money path is unit-tested with fakes.
//
// Per card: pending → created (Square create) → activated (ACTIVATE + read-back
// of state/balance) ; per recipient: one email with all of their cards → emailed.
// A failed row is never retried automatically — only an explicit operator
// resume continues it, and it continues from the last recorded state.

import type { GiftCardRow, GiftCardStatus } from "./store";
import type { SquareGiftCard } from "./square";
import { giftCardEmail } from "./draft";
import type { Award } from "./parse";

export type Recipient = {
  store: string;
  awardMonth: string; // 'YYYY-MM' or 'test-<stamp>'
  clickupUserId: string;
  award: Award | "Test";
  first: string;
  last: string;
  email: string;
  cards: number;
  cardCents: number;
  locationId: string;
  approvedBy: string;
};

export type IssueDeps = {
  byKey(key: string): Promise<GiftCardRow | null>;
  insertPending(row: PendingRow): Promise<void>;
  update(
    key: string,
    patch: { status: GiftCardStatus; square_gift_card_id?: string | null; gan_last4?: string | null; email_message_id?: string | null; error?: string | null },
  ): Promise<void>;
  create(key: string, locationId: string): Promise<SquareGiftCard>;
  activate(key: string, giftCardId: string, locationId: string, cents: number): Promise<void>;
  get(giftCardId: string): Promise<SquareGiftCard>;
  sendEmail(msg: { to: string; subject: string; text: string; html: string }): Promise<{ id: string }>;
  log(line: string): void;
};

export type RecipientResult = {
  recipient: Recipient;
  cards: { key: string; last4: string | null; status: GiftCardStatus; amountCents: number }[];
  emailed: boolean;
  error: string | null;
};

export const MAX_CARD_CENTS = 5000;
export const MAX_CARDS = 4;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function cardKey(r: Pick<Recipient, "store" | "awardMonth" | "clickupUserId">, index: number): string {
  return `rec-${r.store}-${r.awardMonth}-${r.clickupUserId}-${index}`;
}

export function validateRecipient(r: Recipient): string | null {
  if (!r.clickupUserId) return "missing ClickUp user";
  if (!r.first.trim()) return "missing first name";
  if (!EMAIL_RE.test(r.email.trim())) return `invalid email "${r.email}"`;
  if (!Number.isInteger(r.cards) || r.cards < 1 || r.cards > MAX_CARDS) return `cards must be 1–${MAX_CARDS}`;
  if (!Number.isInteger(r.cardCents) || r.cardCents < 100 || r.cardCents > MAX_CARD_CENTS) {
    return `card amount must be $1–$${MAX_CARD_CENTS / 100}`;
  }
  return null;
}

export type PendingRow = ReturnType<typeof pendingRow>;

function pendingRow(r: Recipient, index: number) {
  return {
    store: r.store,
    award_month: r.awardMonth,
    idempotency_key: cardKey(r, index),
    clickup_user_id: r.clickupUserId,
    award: r.award,
    recipient_first: r.first.trim(),
    recipient_last: r.last.trim(),
    recipient_email: r.email.trim(),
    card_index: index,
    amount_cents: r.cardCents,
    location_id: r.locationId,
    approved_by: r.approvedBy,
  };
}

const last4 = (gan: string | undefined) => (gan ? gan.slice(-4) : null);
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e)).slice(0, 300);

export async function issueForRecipient(
  r: Recipient,
  deps: IssueDeps,
  opts: { resume?: boolean } = {},
): Promise<RecipientResult> {
  const result: RecipientResult = { recipient: r, cards: [], emailed: false, error: null };
  const invalid = validateRecipient(r);
  if (invalid) return { ...result, error: invalid };

  const ready: { key: string; giftCardId: string }[] = [];
  for (let i = 1; i <= r.cards; i++) {
    const key = cardKey(r, i);
    let step = "ledger";
    try {
      let row = await deps.byKey(key);
      if (row?.status === "external") {
        result.cards.push({ key, last4: null, status: "external", amountCents: row.amount_cents });
        continue;
      }
      if (!row) {
        await deps.insertPending(pendingRow(r, i));
        row = await deps.byKey(key);
        if (!row) throw new Error("ledger row not visible after insert");
      }
      if (row.status === "failed" && !opts.resume) {
        result.cards.push({ key, last4: row.gan_last4, status: "failed", amountCents: row.amount_cents });
        result.error = `card ${i} failed earlier (${row.error ?? "unknown"}) — use Retry`;
        return result;
      }
      if (row.amount_cents !== r.cardCents) {
        throw new Error(`ledger amount ${row.amount_cents} ≠ requested ${r.cardCents}; not changing a recorded card`);
      }
      let giftCardId = row.square_gift_card_id;
      let lastFour = row.gan_last4;
      if (!giftCardId) {
        step = "create";
        const card = await deps.create(key, r.locationId);
        giftCardId = card.id;
        lastFour = last4(card.gan);
        await deps.update(key, { status: "created", square_gift_card_id: giftCardId, gan_last4: lastFour, error: null });
      }
      step = "verify";
      let card = await deps.get(giftCardId);
      if (card.state !== "ACTIVE") {
        step = "activate";
        await deps.activate(key, giftCardId, r.locationId, r.cardCents);
        step = "verify";
        card = await deps.get(giftCardId);
      }
      const balance = card.balance_money?.amount ?? -1;
      if (card.state !== "ACTIVE" || balance !== r.cardCents) {
        throw new Error(`read-back state=${card.state} balance=${balance} expected ACTIVE ${r.cardCents}`);
      }
      const status: GiftCardStatus = row.status === "emailed" ? "emailed" : "activated";
      if (row.status !== status || row.error) {
        await deps.update(key, { status, gan_last4: last4(card.gan) ?? lastFour, error: null });
      }
      result.cards.push({ key, last4: last4(card.gan) ?? lastFour, status, amountCents: r.cardCents });
      ready.push({ key, giftCardId });
    } catch (e) {
      const err = errText(e);
      deps.log(`[recognition] BREADCRUMB step=${step} key=${key} err=${err}`);
      await deps.update(key, { status: "failed", error: `${step}: ${err}` }).catch(() => {});
      result.cards.push({ key, last4: null, status: "failed", amountCents: r.cardCents });
      result.error = `card ${i} ${step} failed: ${err}`;
      return result;
    }
  }

  if (result.cards.every((c) => c.status === "emailed" || c.status === "external")) {
    return { ...result, emailed: true };
  }
  if (!ready.length) return result;
  try {
    const cards = [];
    for (const c of ready) {
      const card = await deps.get(c.giftCardId);
      cards.push({ gan: card.gan, amountCents: card.balance_money?.amount ?? r.cardCents });
    }
    const msg = giftCardEmail({ first: r.first.trim(), award: r.award, awardMonth: r.awardMonth, cards });
    const sent = await deps.sendEmail({ to: r.email.trim(), ...msg });
    for (const c of ready) await deps.update(c.key, { status: "emailed", email_message_id: sent.id, error: null });
    result.cards = result.cards.map((c) => (c.status === "external" ? c : { ...c, status: "emailed" }));
    return { ...result, emailed: true };
  } catch (e) {
    const err = errText(e);
    deps.log(`[recognition] BREADCRUMB step=email key=${ready[0]?.key} err=${err}`);
    for (const c of ready) await deps.update(c.key, { status: "activated", error: `email: ${err}` }).catch(() => {});
    return { ...result, error: `cards active but email failed: ${err}` };
  }
}
