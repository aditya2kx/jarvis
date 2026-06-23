"""skills/square_api — Square REST API client for BHAGA's nightly data pull.

Replaces the Playwright dashboard scrape (transactions, item sales, KDS) with
authenticated Square API calls so the nightly Cloud Run job never logs into the
Square dashboard (no OTP, no magic link, no Chromium for Square).

Modules:
    auth          — OAuth token storage + auto-refresh via Secret Manager.
    grant         — one-time interactive OAuth authorization-code capture (laptop).
    client        — thin REST helper (base URL, bearer auth, cursor pagination).
    export        — Payments + Orders → synthesized transactions/items CSVs.
    kds_reporting — KDS kitchen metrics via the Reporting API (/v1/load).

The export modules write CSVs into extracted/downloads/ with the EXACT
dashboard-export column layout, so the entire downstream parse → map → BQ path
(skills/square_tips/transactions_backend.py, backfill_from_downloads.py) is
unchanged. Cutover is a single env var: BHAGA_SQUARE_BACKEND=api.
"""
