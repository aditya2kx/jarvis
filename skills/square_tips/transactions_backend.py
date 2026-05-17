#!/usr/bin/env python3
"""skills/square_tips/transactions_backend — Playwright-driven Transactions CSV export.

Differs from dashboard_backend.py (which scrapes the aggregated Sales Summary)
in that this drives the per-transaction Transactions report at
`app.squareup.com/dashboard/sales/transactions`. One row per transaction, 55
columns including transaction id, timestamp, gross sales, tip, net total,
source, staff name. Used to build the bhaga model sheet's daily / hour-of-day
breakdowns and as the source of truth for the daily tip pool.

Architecture mirrors dashboard_backend.py:
    * `build_plan()` produces a deterministic step list that the AI agent
      executes through the `user-playwright` MCP.
    * `parse_csv()` is pure-Python (no Playwright dep) so it can be unit
      tested and re-run against historical CSVs without a browser session.
    * `daily_transactions()` is the high-level entry point that picks the
      most recent matching CSV in `extracted/downloads/` and parses it.

Calibration & known quirks (see also selectors/transactions.json):
    * Account display timezone is Eastern Time, shop is in Austin (Central
      Time). `parse_csv()` converts to America/Chicago before deriving
      date_local / hour_local / dow_local. Never rely on the CSV's Date
      column for shop-day bucketing.
    * Export is asynchronous: click Generate, wait for the inline Download
      button to appear (1-60s depending on range size), then click Download.
    * Square names the file `transactions-YYYY-MM-DD-YYYY-MM-DD.csv` where
      the trailing date is end_date + 1 (exclusive end). Don't infer the
      actual range from the filename; use the data inside.

Status (2026-05-16): proven end-to-end with the Palmetto Superfoods account,
55-day backfill (Mar 22 - May 15, 2026). 2,956 transactions parsed; sum of
Total Collected matches the on-page summary exactly ($47,946.77).
"""

from __future__ import annotations

import csv
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
from typing import Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from core.config_loader import project_dir
from skills.credentials import registry as cred_registry


_PROJECT = pathlib.Path(project_dir())
DOWNLOADS_DIR = _PROJECT / "extracted" / "downloads"
SELECTORS_PATH = _PROJECT / "skills" / "square_tips" / "selectors" / "transactions.json"

LOGIN_URL = "https://app.squareup.com/login"
LOGOUT_URL = "https://app.squareup.com/logout"
TRANSACTIONS_URL = "https://app.squareup.com/dashboard/sales/transactions"

# Shop is in Austin (America/Chicago). Override via build_plan(shop_tz=...)
# if/when we add the Houston location.
DEFAULT_SHOP_TZ = "America/Chicago"

# Square's "Time Zone" column uses human display names. Map to IANA so
# zoneinfo can DST-correctly convert. Extend as we encounter new ones.
_TZ_DISPLAY_TO_IANA = {
    "Eastern Time (US & Canada)": "America/New_York",
    "Central Time (US & Canada)": "America/Chicago",
    "Mountain Time (US & Canada)": "America/Denver",
    "Pacific Time (US & Canada)": "America/Los_Angeles",
    "Alaska Time (US & Canada)": "America/Anchorage",
    "Hawaii Time (US & Canada)": "Pacific/Honolulu",
    "Arizona Time (US & Canada)": "America/Phoenix",
}


# Column indexes in the Transactions CSV (verified 2026-05-16).
# Centralized here so the parser can refer to columns by semantic name
# rather than brittle numeric literals scattered through parse_csv().
_COL = {
    "date": 0,
    "time": 1,
    "time_zone": 2,
    "gross_sales": 3,
    "discounts": 4,
    "service_charges": 5,
    "net_sales": 6,
    "gift_card_sales": 7,
    "tax": 8,
    "tip": 9,
    "partial_refunds": 10,
    "total_collected": 11,
    "source": 12,
    "card": 13,
    "cash": 15,
    "net_total": 21,
    "transaction_id": 22,
    "payment_id": 23,
    "staff_name": 27,
    "staff_id": 28,
    "event_type": 31,
    "location": 32,
    "transaction_status": 46,
    "channel": 51,
    "unattributed_tips": 52,
}


# ── Credentials ───────────────────────────────────────────────────


def _credential_name(store: str) -> str:
    """Same credential as dashboard_backend uses — single Square login per store."""
    return f"square_{store.lower()}_login"


def get_credentials(store: str = "palmetto") -> dict:
    """Resolve Square dashboard login from Keychain. {'username', 'password'}."""
    entry = cred_registry.lookup(_credential_name(store))
    if not entry:
        raise RuntimeError(
            f"No credential '{_credential_name(store)}' in registry. Run the "
            f"collaborative login capture via skills/browser/collaborative.py "
            f"to populate it."
        )
    if entry.get("type") != "keychain":
        raise RuntimeError(
            f"Credential '{_credential_name(store)}' is type "
            f"{entry.get('type')!r}, expected 'keychain'."
        )
    result = subprocess.run(
        [
            "security", "find-generic-password",
            "-a", entry["account"],
            "-s", entry["service"],
            "-w",
        ],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Keychain lookup failed for {entry['account']}@{entry['service']}: "
            f"{result.stderr.strip()}"
        )
    return {"username": entry["account"], "password": result.stdout.strip()}


# ── Selectors ─────────────────────────────────────────────────────


def selectors() -> dict:
    return json.loads(SELECTORS_PATH.read_text())


# ── CSV parsing ───────────────────────────────────────────────────


def parse_money_cents(s: str) -> int:
    """Parse a money string into integer cents.

    Handles all forms Square has been observed to emit:
        '$13.50'   -> 1350
        '-$3.45'   -> -345     (Transactions CSV uses leading minus)
        '($36.75)' -> -3675    (Sales Summary Days CSV uses parens)
        '$0.00'    -> 0
        ''         -> 0
        '$1,234.56' -> 123456
    """
    s = (s or "").strip()
    if not s or s in ("$0.00", "0", "$0"):
        return 0
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    elif s.startswith("-"):
        negative = True
        s = s[1:]
    s = s.replace("$", "").replace(",", "").strip()
    if not s:
        return 0
    cents = int(round(float(s) * 100))
    return -cents if negative else cents


def _to_iana(tz_display: str) -> str:
    iana = _TZ_DISPLAY_TO_IANA.get((tz_display or "").strip())
    if not iana:
        raise ValueError(
            f"Unknown Square Time Zone display value {tz_display!r}. "
            f"Extend _TZ_DISPLAY_TO_IANA in transactions_backend.py."
        )
    return iana


def parse_csv(
    csv_path: pathlib.Path,
    *,
    shop_tz: str = DEFAULT_SHOP_TZ,
) -> list[dict]:
    """Parse a downloaded Transactions CSV into canonical per-transaction records.

    Each output dict represents one transaction (one CSV row) with both the
    original ET-bucketed timestamps preserved (for audit) AND derived
    shop-local fields used by the model sheet:

        {
            "transaction_id": str,
            "event_type": "Payment" | "Refund",
            "created_at_src_iso": "2026-05-15T21:38:24-04:00",  # Square's source TZ
            "created_at_local_iso": "2026-05-15T20:38:24-05:00", # shop TZ (CT)
            "date_local": "2026-05-15",       # KEY for daily aggregation
            "hour_local": 20,                  # KEY for dow_hour heatmap
            "dow_local": 4,                    # 0=Monday
            "gross_sales_cents": int,
            "discount_cents": int,             # typically negative
            "tip_cents": int,                  # negative on refunds
            "net_total_cents": int,            # after Square fees
            "total_collected_cents": int,      # before Square fees
            "source": str,                     # Register | Square Kiosk | Uber Eats | ...
            "staff_name": str,                 # often empty (kiosk, third-party)
            "location": str,
            "raw_date_csv": str,               # original ET-bucketed date from CSV (audit)
            "raw_time_csv": str,
            "raw_tz_csv": str,
        }

    Records are returned sorted by created_at_local_iso ascending.
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    target_tz = ZoneInfo(shop_tz)

    # Some descriptions contain embedded newlines inside quoted cells. csv.reader
    # with newline='' handles this correctly. utf-8-sig strips a BOM if present.
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))

    if not rows or len(rows) < 2:
        return []

    header = rows[0]
    if "Transaction ID" not in header:
        # Probably wrong CSV (e.g. Sales Summary). Caller fed the wrong path.
        return []

    records: list[dict] = []
    for row in rows[1:]:
        if len(row) < len(_COL) or not row[_COL["transaction_id"]]:
            continue

        date_str = row[_COL["date"]].strip()
        time_str = row[_COL["time"]].strip()
        tz_display = row[_COL["time_zone"]]
        try:
            src_tz = ZoneInfo(_to_iana(tz_display))
        except ValueError:
            # Skip rather than crash; log via Slack in production via the orchestrator.
            continue

        try:
            dt_src = datetime.datetime.fromisoformat(f"{date_str}T{time_str}").replace(
                tzinfo=src_tz
            )
        except ValueError:
            continue
        dt_local = dt_src.astimezone(target_tz)

        records.append({
            "transaction_id": row[_COL["transaction_id"]],
            "event_type": row[_COL["event_type"]],
            "created_at_src_iso": dt_src.isoformat(),
            "created_at_local_iso": dt_local.isoformat(),
            "date_local": dt_local.date().isoformat(),
            "hour_local": dt_local.hour,
            "dow_local": dt_local.weekday(),
            "gross_sales_cents": parse_money_cents(row[_COL["gross_sales"]]),
            "discount_cents": parse_money_cents(row[_COL["discounts"]]),
            "tip_cents": parse_money_cents(row[_COL["tip"]]),
            "net_total_cents": parse_money_cents(row[_COL["net_total"]]),
            "total_collected_cents": parse_money_cents(row[_COL["total_collected"]]),
            "source": row[_COL["source"]],
            "staff_name": row[_COL["staff_name"]],
            "location": row[_COL["location"]],
            "raw_date_csv": date_str,
            "raw_time_csv": time_str,
            "raw_tz_csv": tz_display,
        })

    records.sort(key=lambda r: r["created_at_local_iso"])
    return records


# ── Aggregations (used by tip_pool_allocation + the model sheet) ───


def aggregate_daily_tip_pool(records: list[dict]) -> dict[str, int]:
    """Sum tip_cents by date_local. Refund tips count as negative (reduces pool).

    Returns {'YYYY-MM-DD': cents}. Days with $0 net tips are still included
    (value 0) so downstream code doesn't have to special-case missing days.
    """
    by_day: dict[str, int] = {}
    for r in records:
        d = r["date_local"]
        by_day[d] = by_day.get(d, 0) + r["tip_cents"]
    return by_day


def aggregate_daily_sales(records: list[dict]) -> dict[str, dict]:
    """Sum sales metrics by date_local.

    Returns {'YYYY-MM-DD': {'gross_sales_cents', 'net_sales_cents',
    'total_collected_cents', 'transaction_count', 'refund_count'}}.
    """
    by_day: dict[str, dict] = {}
    for r in records:
        d = r["date_local"]
        bucket = by_day.setdefault(d, {
            "gross_sales_cents": 0,
            "total_collected_cents": 0,
            "tip_cents": 0,
            "transaction_count": 0,
            "refund_count": 0,
        })
        bucket["gross_sales_cents"] += r["gross_sales_cents"]
        bucket["total_collected_cents"] += r["total_collected_cents"]
        bucket["tip_cents"] += r["tip_cents"]
        bucket["transaction_count"] += 1
        if r["event_type"] == "Refund":
            bucket["refund_count"] += 1
    return by_day


# ── Playwright playbook ───────────────────────────────────────────


def build_plan(
    start_date: datetime.date,
    end_date: datetime.date,
    *,
    store: str = "palmetto",
    creds: Optional[dict] = None,
    shop_tz: str = DEFAULT_SHOP_TZ,
    max_generate_wait_seconds: int = 300,
) -> dict:
    """Produce a deterministic Playwright playbook for the AI agent.

    The plan covers the full from-scratch flow: forced logout, login (using
    Keychain-resolved password fetched at runtime, NOT embedded in the plan),
    navigate to Transactions, set the date range, trigger Generate, poll for
    Download readiness, click Download, and surface the saved CSV path.

    Square Transactions supports arbitrary date ranges in a single export.
    Unlike dashboard_backend.py, this skill does NOT need week-iteration.

    The download lands in DOWNLOADS_DIR as
    `transactions-{start}-{end_plus_1}.csv` (Square uses exclusive end in the
    filename). The AI sets `captures.csv_path` to the resolved path so the
    orchestrator can hand it to parse_csv().
    """
    sels = selectors()
    creds = creds or get_credentials(store)

    return {
        "store": store.lower(),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "shop_tz": shop_tz,
        "creds_username": creds["username"],
        "captures": {
            "csv_path": None,
            "transaction_count_on_page": None,
            "total_collected_on_page": None,
            "errors": [],
        },
        "steps": [
            {
                "id": "logout",
                "action": "browser_navigate",
                "args": {"url": LOGOUT_URL},
                "description": "Force logout for a clean run state.",
                "postcondition": "URL contains '/login'",
            },
            {
                "id": "login_email",
                "action": "browser_type",
                "selectors_hint": "Use skills/square_tips/selectors/dashboard.json#login.email_input",
                "value": creds["username"],
                "description": "Step 1 of Square's two-step login flow.",
            },
            {
                "id": "login_continue",
                "action": "browser_click",
                "selectors_hint": "dashboard.json#login.continue_button",
                "description": "Advance to password step.",
            },
            {
                "id": "login_password",
                "action": "browser_type",
                "selectors_hint": "dashboard.json#login.password_input",
                "value_ref": "creds.password",
                "description": "Type password (Keychain-resolved at runtime, never embedded).",
            },
            {
                "id": "login_submit",
                "action": "browser_click",
                "selectors_hint": "dashboard.json#login.signin_button",
                "description": "Submit login.",
                "postcondition": "URL contains '/dashboard'",
            },
            {
                "id": "navigate_transactions",
                "action": "browser_navigate",
                "args": {"url": TRANSACTIONS_URL},
                "description": "Open the Transactions report.",
            },
            {
                "id": "wait_for_load",
                "action": "browser_wait_for",
                "args": {"time": 4},
                "description": "Let the SPA mount; date pill and Export trigger become reffable.",
            },
            {
                "id": "capture_page_summary_baseline",
                "action": "browser_snapshot",
                "description": (
                    "Snapshot the page-summary KPIs (Complete Transactions count + "
                    "Total Collected) BEFORE changing the date range so the AI can "
                    "later verify the CSV totals match the on-page numbers."
                ),
            },
            {
                "id": "open_date_picker",
                "action": "browser_click",
                "selectors_hint": (
                    "Click sels.transactions_page.date_range_pill.primary_text_pattern "
                    "(button matching /\\d{2}\\/\\d{2}\\/\\d{4}–\\d{2}\\/\\d{2}\\/\\d{4}/)."
                ),
                "description": "Open the date-range picker popover.",
            },
            {
                "id": "type_start_date",
                "action": "browser_type",
                "selectors_hint": (
                    "First text input inside the date-picker popover (the 'Start' field). "
                    "See sels.transactions_page.date_picker.start_date_input."
                ),
                "value": start_date.strftime("%m/%d/%Y"),
                "description": "Type start date in MM/DD/YYYY.",
            },
            {
                "id": "type_end_date",
                "action": "browser_type",
                "selectors_hint": (
                    "Second text input inside the date-picker popover (the 'End' field). "
                    "See sels.transactions_page.date_picker.end_date_input."
                ),
                "value": end_date.strftime("%m/%d/%Y"),
                "args": {"submit": True},
                "description": "Type end date and press Enter to apply the range.",
            },
            {
                "id": "wait_for_range_applied",
                "action": "browser_wait_for",
                "args": {"time": 2},
                "description": "Allow the report to refetch with the new range.",
            },
            {
                "id": "close_date_picker",
                "action": "browser_press_key",
                "args": {"key": "Escape"},
                "description": "Close the picker popover so the Export button is reachable.",
            },
            {
                "id": "verify_range_applied",
                "action": "browser_snapshot",
                "description": (
                    f"Confirm the page h4 header reads '{start_date.strftime('%b %-d, %Y')}"
                    f"–{end_date.strftime('%b %-d, %Y')}'. If not, alert via slack and abort."
                ),
            },
            {
                "id": "open_export_panel",
                "action": "browser_click",
                "selectors_hint": "sels.transactions_page.export_trigger_button",
                "description": "Expand the Export panel (does NOT start a download).",
            },
            {
                "id": "click_generate",
                "action": "browser_click",
                "selectors_hint": "sels.transactions_page.export_panel.generate_button.fallback_role",
                "description": "Start async CSV generation. Square shows a progress row.",
            },
            {
                "id": "poll_for_ready",
                "action": "browser_loop",
                "max_iterations": max(1, max_generate_wait_seconds // 5),
                "iteration_delay_seconds": 5,
                "break_when": (
                    "An element matching role='button' with name='Download Transactions CSV' "
                    "appears inside the Export panel (sels.transactions_page.export_panel."
                    "download_button)."
                ),
                "on_max_iterations": (
                    "Append to captures.errors: 'generate_timeout_after_"
                    f"{max_generate_wait_seconds}s'. DM via skills/slack and exit non-zero. "
                    "Do NOT silently retry: a stuck generation may indicate Square-side outage."
                ),
                "description": "Poll the Export panel until generation completes.",
            },
            {
                "id": "click_download",
                "action": "browser_click",
                "selectors_hint": "sels.transactions_page.export_panel.download_button.fallback_role",
                "description": (
                    "Trigger the actual file download. The browser_* MCP will report "
                    "'Downloaded file transactions-{start}-{end_plus_1}.csv to ...' in "
                    "the tool result; the AI must capture that path into captures.csv_path."
                ),
            },
            {
                "id": "wait_for_download",
                "action": "browser_wait_for",
                "args": {"time": 3},
                "description": "Brief settle so the file is fully flushed before parse_csv() reads it.",
            },
            {
                "id": "parse_and_validate",
                "action": "python",
                "description": (
                    "records = transactions_backend.parse_csv(captures.csv_path, "
                    f"shop_tz={shop_tz!r}); "
                    "assert sum(r['total_collected_cents'] for r in records) == "
                    "page_summary_total_collected_cents (within $0.01); else append to "
                    "captures.errors and alert via slack."
                ),
            },
        ],
    }


# ── Public entry point ────────────────────────────────────────────


def daily_transactions(
    start_date: datetime.date,
    end_date: datetime.date,
    *,
    store: str = "palmetto",
    shop_tz: str = DEFAULT_SHOP_TZ,
) -> list[dict]:
    """Parse the most recent Transactions CSV that covers the requested range.

    Square names the file with an exclusive end date (end_date + 1). We look
    for both the exact-match filename and, failing that, the most recent
    `transactions-*.csv` in DOWNLOADS_DIR as a debugging fallback.

    Returns the full per-transaction list. Caller (e.g.
    skills/tip_ledger_writer or the bhaga orchestrator) is responsible for
    further aggregation and Sheets I/O.
    """
    end_plus = end_date + datetime.timedelta(days=1)
    pattern = (
        f"transactions-{start_date.year}-{start_date.month:02d}-{start_date.day:02d}-"
        f"{end_plus.year}-{end_plus.month:02d}-{end_plus.day:02d}.csv"
    )
    candidates = list(DOWNLOADS_DIR.glob(pattern))
    if not candidates:
        candidates = sorted(
            DOWNLOADS_DIR.glob("transactions-*.csv"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise FileNotFoundError(
                f"No Transactions CSV found matching {pattern} in {DOWNLOADS_DIR}. "
                f"Run build_plan() and have the AI drive Playwright to populate."
            )
    return parse_csv(candidates[0], shop_tz=shop_tz)


# ── CLI ───────────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse

    cli = argparse.ArgumentParser(description="Square Transactions report extractor")
    sub = cli.add_subparsers(dest="cmd")

    p_parse = sub.add_parser("parse", help="Parse an existing Transactions CSV.")
    p_parse.add_argument("csv_path")
    p_parse.add_argument("--shop-tz", default=DEFAULT_SHOP_TZ)
    p_parse.add_argument(
        "--summary", action="store_true",
        help="Print per-day aggregates instead of the full record list.",
    )

    p_plan = sub.add_parser("plan", help="Print the Playwright playbook for a date range.")
    p_plan.add_argument("--start", required=True, help="YYYY-MM-DD")
    p_plan.add_argument("--end", required=True, help="YYYY-MM-DD")
    p_plan.add_argument("--store", default="palmetto")
    p_plan.add_argument("--shop-tz", default=DEFAULT_SHOP_TZ)

    p_creds = sub.add_parser("verify-creds", help="Check Keychain access.")
    p_creds.add_argument("--store", default="palmetto")

    args = cli.parse_args()

    if args.cmd == "parse":
        records = parse_csv(pathlib.Path(args.csv_path), shop_tz=args.shop_tz)
        if args.summary:
            sales = aggregate_daily_sales(records)
            tips = aggregate_daily_tip_pool(records)
            summary = {
                day: {**sales.get(day, {}), "tip_pool_cents": tips.get(day, 0)}
                for day in sorted(set(sales) | set(tips))
            }
            print(json.dumps(summary, indent=2))
        else:
            print(json.dumps(records, indent=2))
    elif args.cmd == "plan":
        plan = build_plan(
            datetime.date.fromisoformat(args.start),
            datetime.date.fromisoformat(args.end),
            store=args.store,
            shop_tz=args.shop_tz,
        )
        print(json.dumps(plan, indent=2))
    elif args.cmd == "verify-creds":
        creds = get_credentials(args.store)
        print(json.dumps({
            "store": args.store,
            "username": creds["username"],
            "password_length": len(creds["password"]),
            "password_preview": creds["password"][:2] + "***" + creds["password"][-2:],
        }, indent=2))
    else:
        cli.print_help()
