/**
 * Heuristics for marking intra-business transfers among linked Plaid accounts
 * (e.g. checking → own Chase card). Conservative: prefer false negatives;
 * operator can toggle Internal in the console.
 */

export interface LinkedAccountHint {
  account_id: string;
  mask: string | null;
  type: string | null;
}

export interface TxnInternalHint {
  transaction_id: string;
  account_id: string | null;
  name: string | null;
  merchant_name: string | null;
  amount: number;
  date: string;
  pfc_primary: string | null;
  pfc_detailed: string | null;
}

const CARD_ENDING = /card ending in\s*(\d{4})/i;
const THANK_YOU = /payment thank you/i;
const AUTOMATIC_PAYMENT = /automatic payment\s*-?\s*thank/i;

export function suggestInternal(
  txn: TxnInternalHint,
  linked: LinkedAccountHint[],
  peers: TxnInternalHint[],
): boolean {
  const masks = new Set(
    linked.map((a) => (a.mask || "").trim()).filter((m) => m.length === 4),
  );
  const byId = new Map(linked.map((a) => [a.account_id, a]));
  const acct = txn.account_id ? byId.get(txn.account_id) : undefined;
  const text = `${txn.name || ""} ${txn.merchant_name || ""}`;

  const cardMatch = text.match(CARD_ENDING);
  if (cardMatch && masks.has(cardMatch[1])) return true;

  if (
    acct?.type === "credit" &&
    (THANK_YOU.test(text) || AUTOMATIC_PAYMENT.test(text)) &&
    txn.amount < 0
  ) {
    return true;
  }

  if (
    txn.pfc_detailed === "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT" &&
    acct?.type === "depository" &&
    txn.amount > 0
  ) {
    // Paying a card from checking — treat as internal when we also see the
    // opposite leg on a linked credit account (±1 day, matching |amount|).
    if (hasOppositeLeg(txn, peers, linked, "credit")) return true;
  }

  if (
    (txn.pfc_primary === "TRANSFER_OUT" || txn.pfc_primary === "TRANSFER_IN") &&
    hasOppositeLeg(txn, peers, linked, null)
  ) {
    return true;
  }

  return false;
}

function hasOppositeLeg(
  txn: TxnInternalHint,
  peers: TxnInternalHint[],
  linked: LinkedAccountHint[],
  requirePeerType: string | null,
): boolean {
  const linkedIds = new Set(linked.map((a) => a.account_id));
  const byId = new Map(linked.map((a) => [a.account_id, a]));
  const target = Math.abs(txn.amount);
  if (!(target > 0) || !txn.account_id) return false;
  const day = txn.date;

  for (const p of peers) {
    if (p.transaction_id === txn.transaction_id) continue;
    if (!p.account_id || p.account_id === txn.account_id) continue;
    if (!linkedIds.has(p.account_id)) continue;
    if (requirePeerType) {
      const t = byId.get(p.account_id)?.type;
      if (t !== requirePeerType) continue;
    }
    if (Math.abs(Math.abs(p.amount) - target) > 0.01) continue;
    // Opposite sign preferred (outflow vs inflow).
    if (Math.sign(p.amount) === Math.sign(txn.amount) && Math.sign(txn.amount) !== 0) {
      continue;
    }
    if (Math.abs(dayDiff(day, p.date)) <= 1) return true;
  }
  return false;
}

function dayDiff(a: string, b: string): number {
  const ms = Date.parse(a) - Date.parse(b);
  return Math.round(ms / (24 * 60 * 60 * 1000));
}
