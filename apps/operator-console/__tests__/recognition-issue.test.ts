import { describe, expect, it } from "vitest";

import { cardKey, issueForRecipient, type IssueDeps, type Recipient } from "@/lib/recognition/issue";
import type { GiftCardRow } from "@/lib/recognition/store";

const recipient: Recipient = {
  store: "palmetto",
  awardMonth: "2026-09",
  clickupUserId: "3",
  award: "MVP",
  first: "Avery",
  last: "Stone",
  email: "avery@example.com",
  cards: 2,
  cardCents: 1500,
  locationId: "LOC",
  approvedBy: "op@example.com",
};

function fakes(opts: { failActivateOnce?: boolean; failEmail?: boolean } = {}) {
  const ledger = new Map<string, GiftCardRow>();
  const cards = new Map<string, { gan: string; state: string; balance: number }>();
  const calls: string[] = [];
  let activateFails = opts.failActivateOnce ?? false;
  const deps: IssueDeps = {
    byKey: async (k) => (ledger.has(k) ? { ...ledger.get(k)! } : null),
    insertPending: async (row) => {
      calls.push(`insert ${row.idempotency_key}`);
      ledger.set(row.idempotency_key, {
        ...row,
        square_gift_card_id: null,
        gan_last4: null,
        status: "pending",
        email_message_id: null,
        error: null,
        created_at: "",
        updated_at: "",
      });
    },
    update: async (k, p) => {
      const row = ledger.get(k)!;
      ledger.set(k, {
        ...row,
        status: p.status,
        square_gift_card_id: p.square_gift_card_id ?? row.square_gift_card_id,
        gan_last4: p.gan_last4 ?? row.gan_last4,
        email_message_id: p.email_message_id ?? row.email_message_id,
        error: p.error ?? null,
      });
    },
    create: async (k) => {
      calls.push(`create ${k}`);
      const id = `gc-${cards.size + 1}`;
      cards.set(id, { gan: `767400000000${1000 + cards.size}`, state: "PENDING", balance: 0 });
      return { id, gan: cards.get(id)!.gan, state: "PENDING" };
    },
    activate: async (k, id, _loc, cents) => {
      calls.push(`activate ${k}`);
      if (activateFails) {
        activateFails = false;
        throw new Error("Square 500");
      }
      const c = cards.get(id)!;
      cards.set(id, { ...c, state: "ACTIVE", balance: cents });
    },
    get: async (id) => {
      const c = cards.get(id)!;
      return { id, gan: c.gan, state: c.state, balance_money: { amount: c.balance, currency: "USD" } };
    },
    sendEmail: async (msg) => {
      calls.push(`email ${msg.to}`);
      if (opts.failEmail) throw new Error("Gmail 403");
      expect(msg.text).toContain("7674000000001000");
      expect(msg.text).toContain("7674000000001001");
      return { id: "mail-1" };
    },
    log: (l) => calls.push(l),
  };
  return { deps, ledger, calls };
}

describe("issueForRecipient", () => {
  it("happy path: create + activate each card, one email with both GANs", async () => {
    const f = fakes();
    const r = await issueForRecipient(recipient, f.deps);
    expect(r.error).toBeNull();
    expect(r.emailed).toBe(true);
    expect(f.calls.filter((c) => c.startsWith("create"))).toHaveLength(2);
    expect(f.calls.filter((c) => c.startsWith("email"))).toHaveLength(1);
    const row = f.ledger.get(cardKey(recipient, 1))!;
    expect(row).toMatchObject({ status: "emailed", gan_last4: "1000", email_message_id: "mail-1" });
    expect(JSON.stringify([...f.ledger.values()])).not.toContain("7674000000001000");
  });

  it("re-running after success makes no Square writes and sends no email", async () => {
    const f = fakes();
    await issueForRecipient(recipient, f.deps);
    const before = f.calls.length;
    const again = await issueForRecipient(recipient, f.deps);
    expect(again.emailed).toBe(true);
    expect(f.calls.slice(before).filter((c) => /^(create|activate|email)/.test(c))).toEqual([]);
  });

  it("activate failure marks failed, is not auto-retried, and resume continues without a second create", async () => {
    const f = fakes({ failActivateOnce: true });
    const first = await issueForRecipient(recipient, f.deps);
    expect(first.error).toContain("card 1 activate failed");
    expect(f.calls.some((c) => c.includes("BREADCRUMB step=activate"))).toBe(true);
    expect(f.ledger.get(cardKey(recipient, 1))!.status).toBe("failed");

    const blocked = await issueForRecipient(recipient, f.deps);
    expect(blocked.error).toContain("use Retry");

    const resumed = await issueForRecipient(recipient, f.deps, { resume: true });
    expect(resumed.emailed).toBe(true);
    expect(f.calls.filter((c) => c === `create ${cardKey(recipient, 1)}`)).toHaveLength(1);
  });

  it("email failure leaves cards activated with an error and reports it", async () => {
    const f = fakes({ failEmail: true });
    const r = await issueForRecipient(recipient, f.deps);
    expect(r.emailed).toBe(false);
    expect(r.error).toContain("email failed");
    expect(f.ledger.get(cardKey(recipient, 2))).toMatchObject({ status: "activated", error: "email: Gmail 403" });
  });

  it("cards marked as issued by hand are never created or emailed", async () => {
    const f = fakes();
    await issueForRecipient(recipient, f.deps);
    for (const row of f.ledger.values()) f.ledger.set(row.idempotency_key, { ...row, status: "external" });
    const before = f.calls.length;
    const r = await issueForRecipient(recipient, f.deps, { resume: true });
    expect(r.error).toBeNull();
    expect(r.emailed).toBe(true);
    expect(f.calls.slice(before).filter((c) => /^(create|activate|email)/.test(c))).toEqual([]);
    expect(f.ledger.get(cardKey(recipient, 1))!.status).toBe("external");
  });

  it("rejects invalid input before any side effect", async () => {
    const f = fakes();
    const r = await issueForRecipient({ ...recipient, email: "nope", cardCents: 10_000 }, f.deps);
    expect(r.error).toContain("invalid email");
    expect(f.calls).toEqual([]);
  });
});
