# skills/square_tips

Square Payments aggregator. Pulls daily tip totals (card tips) for a given Square `location_id` and date range via the Square Payments API. Reusable by any Jarvis agent operating against a Square POS.

**Status:** scaffold only. To be implemented in BHAGA milestone M1.

## What it does (planned)

- Authenticate with a Square personal access token from macOS Keychain (via `skills/credentials/`)
- Call `GET /v2/payments` paginated via `cursor`, scoped by `begin_time` / `end_time` / `location_id`
- Filter `status == COMPLETED` to exclude voids/refunds
- Sum `tip_money.amount` grouped by **local date** (default `America/Chicago` for Austin; pass `tz` for other stores)
- Return `[{date, tip_total_cents, card_tip_cents, payment_count}]`

## Why a separate skill (not inline in the agent)

The Square Payments API will be useful for other agents too — AKSHAYA already extracts orders from Square via Playwright and is on the backlog to migrate to the API. When that happens, both agents share auth + pagination + retry logic via this skill.

## Public API (planned)

```python
from skills.square_tips import daily_tips

records = daily_tips(
    location_id="L...",
    start_date="2026-04-01",
    end_date="2026-04-14",
    tz="America/Chicago",
)
# -> [{"date": "2026-04-01", "tip_total_cents": 12450, "card_tip_cents": 12450, "payment_count": 41}, ...]
```

All money values are **integer cents** — never floats. Currency math is integer math.

## Auth setup (planned)

1. Sign up at developers.squareup.com (free, self-serve)
2. Create a personal access token with scope `PAYMENTS_READ` (and optionally `ORDERS_READ` for cross-reference)
3. Store in Keychain via `skills.credentials.registry`:
   ```bash
   security add-generic-password -a SQUARE_ACCESS_TOKEN -s jarvis -w "EAA..."
   ```
4. Register the credential metadata in `skills/credentials/registry.json` (no secret value, just metadata)

## Out of scope

- Cash tips (not visible to Square — declared elsewhere)
- Per-employee tip attribution (Square Team is not configured at the shop in v1)
- Refund/dispute reconciliation (v2)

## Multi-store

`location_id` is a parameter — Houston (September 2026) drops in by passing its own location ID and reusing the same access token (or a separate token per shop, depending on franchise structure; defer until Houston onboarding).
