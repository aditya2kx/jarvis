#!/usr/bin/env python3
"""skills/square_tips — public adapter.

Returns daily tip records in a canonical schema regardless of which backend
fetches them. Backends auto-resolve based on what credentials are available:

    1. API backend (skills/square_tips/api_backend.py)
       - Active when a Square Personal Access Token is in Keychain at
         service `jarvis-square-<store>`, account `SQUARE_ACCESS_TOKEN_<STORE>`.
       - Calls Square Payments API directly. Faster, more stable.
       - Requires Developer Console access (owner-only on Square corporate accounts).

    2. Dashboard backend (skills/square_tips/dashboard_backend.py)
       - Active when a Square login is in Keychain at service
         `jarvis-square-<store_or_workspace>`, account = email.
       - Drives Playwright through app.squareup.com Sales Summary report.
       - Used when API backend is blocked (e.g. corporate account, non-owner user).

Per `jarvis.md` Hard Lesson #5: browser is a stepping stone, not the destination.
The day API access is granted, ONLY the backend resolution flips — the public
`daily_tips()` interface and downstream callers (BHAGA's pull_tips, the tip ledger
writer) don't change at all.

Canonical record schema:
    {
        "date": "YYYY-MM-DD",        # ISO date string, in store's local TZ
        "tip_total_cents": int,       # all tips that day (card + cash if reported)
        "card_tip_cents": int,        # card tips only (sub-component)
        "cash_tip_cents": int,        # declared cash tips (0 if Square doesn't track)
        "payment_count": int | None,  # # payments / sales transactions that day
        "source": "api" | "dashboard",
    }
"""

from __future__ import annotations

import datetime
from typing import Iterable, Literal, Optional

from skills.credentials import registry as cred_registry


def _has_api_token(store: str) -> bool:
    entry = cred_registry.lookup(f"square_{store.lower()}")
    return bool(entry) and entry.get("type") == "keychain"


def _has_dashboard_login(store: str) -> bool:
    entry = cred_registry.lookup(f"square_{store.lower()}_login")
    return bool(entry) and entry.get("type") == "keychain"


def daily_tips(
    start_date: datetime.date,
    end_date: datetime.date,
    *,
    store: str = "palmetto",
    backend: Optional[Literal["api", "dashboard"]] = None,
) -> list[dict]:
    """Fetch daily tip records for a store across a date range.

    Args:
        start_date: First date to include (inclusive).
        end_date: Last date to include (inclusive).
        store: Logical store name — resolves Keychain credential entry.
               'palmetto' for the corporate Square account hosting Austin (and
               future Houston etc. — single corporate account managed by chain
               owner per 2026-04-19 user note).
        backend: Override auto-resolution. None = auto-pick API > dashboard.

    Returns:
        List of canonical records (see module docstring), sorted by date.
    """
    if backend is None:
        if _has_api_token(store):
            backend = "api"
        elif _has_dashboard_login(store):
            backend = "dashboard"
        else:
            raise RuntimeError(
                f"No Square credentials in Keychain for store={store!r}. "
                f"Run skills/square_app_provisioning/ for API access, OR "
                f"capture dashboard login via skills/browser/collaborative.py + "
                f"store under credential name 'square_{store.lower()}_login'."
            )

    if backend == "api":
        from skills.square_tips import api_backend
        return api_backend.daily_tips(start_date, end_date, store=store)
    if backend == "dashboard":
        from skills.square_tips import dashboard_backend
        return dashboard_backend.daily_tips(start_date, end_date, store=store)

    raise ValueError(f"Unknown backend: {backend!r}")


def iter_weeks(start: datetime.date, end: datetime.date) -> Iterable[tuple[datetime.date, datetime.date]]:
    """Yield (monday, sunday) date pairs covering [start, end].

    Used by the dashboard backend because Square Sales Summary CSV in Days
    mode caps at one week of columns per export. The API backend doesn't have
    this constraint but uses the same iterator for consistency.
    """
    cursor = start - datetime.timedelta(days=start.weekday())  # back to Monday
    final_monday = end - datetime.timedelta(days=end.weekday())
    while cursor <= final_monday:
        yield cursor, cursor + datetime.timedelta(days=6)
        cursor = cursor + datetime.timedelta(days=7)
