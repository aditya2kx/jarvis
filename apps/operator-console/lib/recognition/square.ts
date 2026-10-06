import "server-only";

import { readSecret } from "@/lib/recognition/secrets";

// Square Gift Cards API for Monthly recognition (Issue #369). Token is the
// shared BHAGA OAuth secret `square_palmetto_oauth` (refreshed by the Python
// pipeline — skills/square_api/auth.py); needs GIFTCARDS_READ + GIFTCARDS_WRITE
// (skills/square_api/grant.py OAUTH_SCOPES).

const API_BASE = "https://connect.squareup.com";
const SQUARE_VERSION = "2025-01-23";
const SECRET_ID = "square_palmetto_oauth";
export const COMP_INSTRUMENT_ID = "palmetto-recognition-comp";

export type SquareGiftCard = {
  id: string;
  gan: string;
  state: string;
  balance_money?: { amount: number; currency: string };
};

async function token(): Promise<string> {
  const s = JSON.parse(await readSecret(SECRET_ID)) as { access_token?: string; expires_at?: string };
  if (!s.access_token) throw new Error("square_palmetto_oauth has no access_token");
  if (s.expires_at && Date.parse(s.expires_at) < Date.now()) {
    throw new Error("Square access token expired — run skills/square_api refresh (nightly pipeline refreshes it)");
  }
  return s.access_token;
}

async function squareFetch(path: string, init: { method?: string; body?: unknown } = {}): Promise<Record<string, unknown>> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: init.method ?? "GET",
    headers: {
      Authorization: `Bearer ${await token()}`,
      "Square-Version": SQUARE_VERSION,
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: init.body ? JSON.stringify(init.body) : undefined,
  });
  const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  if (!res.ok) {
    const errors = (body.errors as { code?: string; detail?: string }[] | undefined) ?? [];
    const msg = errors.map((e) => `${e.code}: ${e.detail}`).join("; ") || String(res.status);
    throw new Error(`Square ${init.method ?? "GET"} ${path} → ${res.status} ${msg}`);
  }
  return body;
}

/** Can this token read gift cards? Returns null when OK, else the reason. */
export async function giftCardScopeProblem(): Promise<string | null> {
  try {
    await squareFetch("/v2/gift-cards?limit=1");
    return null;
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return /INSUFFICIENT_SCOPES|403|401/.test(msg)
      ? "Square token lacks GIFTCARDS_READ/WRITE — re-run skills/square_api/grant.py"
      : msg;
  }
}

export async function createGiftCard(key: string, locationId: string): Promise<SquareGiftCard> {
  const body = await squareFetch("/v2/gift-cards", {
    method: "POST",
    body: { idempotency_key: `${key}-create`, location_id: locationId, gift_card: { type: "DIGITAL" } },
  });
  return body.gift_card as SquareGiftCard;
}

export async function activateGiftCard(
  key: string,
  giftCardId: string,
  locationId: string,
  amountCents: number,
): Promise<void> {
  await squareFetch("/v2/gift-cards/activities", {
    method: "POST",
    body: {
      idempotency_key: `${key}-activate`,
      gift_card_activity: {
        type: "ACTIVATE",
        location_id: locationId,
        gift_card_id: giftCardId,
        activate_activity_details: {
          amount_money: { amount: amountCents, currency: "USD" },
          buyer_payment_instrument_ids: [COMP_INSTRUMENT_ID],
          reference_id: key,
        },
      },
    },
  });
}

export async function getGiftCard(giftCardId: string): Promise<SquareGiftCard> {
  const body = await squareFetch(`/v2/gift-cards/${encodeURIComponent(giftCardId)}`);
  return body.gift_card as SquareGiftCard;
}
