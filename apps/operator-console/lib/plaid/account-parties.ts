/**
 * Resolve from/to account sides for Plaid ledger rows.
 * Plaid amount > 0 = money leaving the linked account (outflow).
 */

const CARD_ENDING = /(?:card\s+)?ending\s+in\s*(\d{4})/i;
const HASH_MASK = /#{4,}(\d{4})\b/;
const LAST4_BARE = /\b(?:to|from|acct|account|x{2,}|•{2,})\s*[#x•]*\s*(\d{4})\b/i;

export function digitsMask(raw: string | null | undefined): string {
  const d = (raw || "").replace(/\D/g, "");
  return d.length >= 4 ? d.slice(-4) : "";
}

/** Best-effort other-party last-4 from bank memo / merchant text. */
export function extractCounterpartyMask(
  ...parts: Array<string | null | undefined>
): string {
  const text = parts.filter(Boolean).join(" ");
  if (!text) return "";
  const card = text.match(CARD_ENDING);
  if (card?.[1]) return card[1];
  const hash = text.match(HASH_MASK);
  if (hash?.[1]) return hash[1];
  const bare = text.match(LAST4_BARE);
  if (bare?.[1]) return bare[1];
  return "";
}

export type AccountPartySide = {
  mask: string;
  label: string;
};

export type FromToParties = {
  from: AccountPartySide;
  to: AccountPartySide;
  /** Linked Plaid account last-4 (our side). */
  our_mask: string;
  /** Parsed other-party last-4 from memo when present. */
  other_mask: string;
};

function formatSide(mask: string, name: string | null | undefined, fallback: string): string {
  const m = digitsMask(mask);
  const n = (name || "").trim();
  if (m && n) return `•••• ${m} · ${n}`;
  if (m) return `•••• ${m}`;
  if (n) return n;
  return fallback;
}

/**
 * Directional from → to for display and rule matching.
 * Outflow (amount > 0): from = our linked account, to = counterparty/other mask.
 * Inflow (amount < 0): from = counterparty/other, to = our linked account.
 */
export function resolveFromTo(input: {
  amount: number;
  our_mask: string | null | undefined;
  our_label?: string | null;
  name?: string | null;
  merchant_name?: string | null;
  counterparty_name?: string | null;
}): FromToParties {
  const our_mask = digitsMask(input.our_mask);
  const other_mask = extractCounterpartyMask(
    input.name,
    input.merchant_name,
    input.counterparty_name,
  );
  const other_name =
    (input.counterparty_name || "").trim() ||
    (input.merchant_name || "").trim() ||
    null;
  const ourLabel = formatSide(our_mask, input.our_label, "Our account");
  const otherLabel = formatSide(other_mask, other_name, other_mask ? `•••• ${other_mask}` : "—");

  const our: AccountPartySide = { mask: our_mask, label: ourLabel };
  const other: AccountPartySide = { mask: other_mask, label: otherLabel };

  if (input.amount < 0) {
    return { from: other, to: our, our_mask, other_mask };
  }
  return { from: our, to: other, our_mask, other_mask };
}
