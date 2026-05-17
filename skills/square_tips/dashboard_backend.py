#!/usr/bin/env python3
"""skills/square_tips/dashboard_backend — Playwright-driven Sales Summary CSV export.

Used when API backend is unavailable (e.g. Palmetto corporate Square account
where individual store owners don't have Developer Console access). Same
public interface as api_backend so the swap is transparent to callers.

Architecture mirrors skills/slack_app_provisioning/ + skills/square_app_provisioning/:
the heavy Playwright orchestration is structured as a `build_plan()` function
that returns a deterministic step-by-step playbook for the AI agent driving the
`user-playwright` MCP. The Python here also exposes `parse_csv()` and helper
functions that work standalone (for testing + post-Playwright work).

Status (2026-04-19): proven from-scratch end-to-end on app.squareup.com Sales
Summary Beta with the Palmetto Superfoods corporate account. See
`skills/square_tips/selectors/dashboard.json` for calibrated selectors with
last_verified date.

KNOWN LIMITATIONS / TODOs:
    - Date range setter not yet calibrated. Current default range persists per
      Square account session; for arbitrary date ranges, the date_range_pill
      selector + date picker UI need calibration. Workaround: use the Previous/
      Next 7-day buttons and step through.
    - Report type defaults to 'Summary' for new sessions; build_plan() includes
      the steps to switch to 'Days' but they only need to run once per Square
      account (the setting is sticky).
    - reCAPTCHA on login may challenge if Square detects automation patterns;
      currently passive-only on the verified account.
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

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from core.config_loader import project_dir
from skills.credentials import registry as cred_registry


_PROJECT = pathlib.Path(project_dir())
DOWNLOADS_DIR = _PROJECT / "extracted" / "downloads"
SELECTORS_PATH = _PROJECT / "skills" / "square_tips" / "selectors" / "dashboard.json"

LOGIN_URL = "https://app.squareup.com/login"
LOGOUT_URL = "https://app.squareup.com/logout"
SALES_SUMMARY_URL = "https://app.squareup.com/dashboard/sales/reports/sales-summary"


# ── Credentials ───────────────────────────────────────────────────


def _credential_name(store: str) -> str:
    return f"square_{store.lower()}_login"


def get_credentials(store: str = "palmetto") -> dict:
    """Resolve Square dashboard login from Keychain.

    Returns {'username': email, 'password': str}. The password fetch uses
    the `security` command directly (skills.credentials.registry returns
    metadata only, not secret values).
    """
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
    """Load the calibrated selectors JSON. Caller checks last_verified."""
    return json.loads(SELECTORS_PATH.read_text())


# ── CSV parsing ───────────────────────────────────────────────────


def parse_money_cents(s: str) -> int:
    """'$1,234.56' or '($36.75)' → 123456 or -3675. '$0.00' → 0."""
    s = (s or "").strip()
    if not s or s in ("$0.00", "0", "$0"):
        return 0
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace("$", "").replace(",", "").strip()
    if not s:
        return 0
    cents = int(round(float(s) * 100))
    return -cents if negative else cents


def parse_int_count(s: str) -> Optional[int]:
    """'53' or '53 orders' → 53; '' → None."""
    s = (s or "").strip()
    if not s:
        return None
    match = re.match(r"(\d+)", s)
    return int(match.group(1)) if match else None


def parse_csv(csv_path: pathlib.Path) -> list[dict]:
    """Parse a downloaded Sales Summary CSV (Days mode) into canonical records.

    Days-mode CSV format (verified 2026-04-19):
        Row 1: header. First cell = 'Sales summary - Daily\\nAll day (...CT)'.
               Subsequent cells = dates as 'M/D/YYYY' (no zero padding).
        Subsequent rows: each row's first cell is the metric name; subsequent
               cells are the per-day values aligned with the date columns.

    Returns sorted list of canonical records (see adapter.py docstring).
    Returns [] if the CSV isn't in Days mode (e.g. Summary mode export).
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    # Square's CSV ships with a UTF-8 BOM AND embeds CRLF line breaks INSIDE
    # the first quoted cell ("Sales summary - Daily\r\nAll day (...CT)").
    # Python's csv.reader requires `newline=''` and BOM stripping (utf-8-sig)
    # to parse the multi-line quoted cell as a single header field.
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))

    if not rows or len(rows) < 2:
        return []

    header = rows[0]
    if not header or "Daily" not in (header[0] or ""):
        # Likely Summary mode (first cell is "Sales summary - Summary..."); skip.
        return []

    # Parse date columns from header[1:]. Format is M/D/YYYY.
    date_cols: list[tuple[int, datetime.date]] = []
    for i, cell in enumerate(header[1:], start=1):
        try:
            d = datetime.datetime.strptime(cell.strip(), "%m/%d/%Y").date()
            date_cols.append((i, d))
        except (ValueError, AttributeError):
            continue
    if not date_cols:
        return []

    # Locate the rows we care about by their first-cell label.
    by_label: dict[str, list[str]] = {}
    for row in rows[1:]:
        if not row or not row[0]:
            continue
        by_label[row[0].strip()] = row

    tips_row = by_label.get("Tips")
    if not tips_row:
        return []
    cash_row = by_label.get("Cash")
    card_row = by_label.get("Card")
    txn_row = (
        by_label.get("Tips transactions")
        or by_label.get("Total payments collected transactions")
        or by_label.get("Total number of sales")
    )

    records = []
    for col_idx, day in date_cols:
        tip_cents = parse_money_cents(tips_row[col_idx]) if col_idx < len(tips_row) else 0
        # Square's Sales Summary doesn't break tips out by card vs cash unless
        # the merchant uses Square Team. For Palmetto today, cash tips reported
        # is $0, so all tips are effectively card. Capture both for correctness.
        card_cents = (
            parse_money_cents(card_row[col_idx])
            if card_row and col_idx < len(card_row)
            else 0
        )
        cash_cents = (
            parse_money_cents(cash_row[col_idx])
            if cash_row and col_idx < len(cash_row)
            else 0
        )
        # When card payments are nonzero and cash is zero, all tips are from card.
        card_tip_cents = tip_cents if cash_cents == 0 and card_cents > 0 else 0
        cash_tip_cents = 0  # Square doesn't separately report declared cash tips here

        txn_count = (
            parse_int_count(txn_row[col_idx])
            if txn_row and col_idx < len(txn_row)
            else None
        )

        records.append({
            "date": day.isoformat(),
            "tip_total_cents": tip_cents,
            "card_tip_cents": card_tip_cents,
            "cash_tip_cents": cash_tip_cents,
            "payment_count": txn_count,
            "source": "dashboard",
        })

    records.sort(key=lambda r: r["date"])
    return records


# ── Playwright playbook ───────────────────────────────────────────


def build_plan(
    start_date: datetime.date,
    end_date: datetime.date,
    *,
    store: str = "palmetto",
    creds: Optional[dict] = None,
) -> dict:
    """Produce a structured plan for the AI to drive against `user-playwright`.

    Mirrors the pattern in skills/slack_app_provisioning/provision.py and
    skills/square_app_provisioning/provision.py — the AI executes each step
    via the appropriate browser_* MCP tool, snapshotting between steps to
    refresh refs.

    The plan assumes:
      - The user is starting in any state (logged in, logged out, or expired).
      - Selectors live in `skills/square_tips/selectors/dashboard.json`.
      - The captured CSV will appear in `extracted/downloads/`.
    """
    sels = selectors()
    creds = creds or get_credentials(store)

    return {
        "store": store.lower(),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "creds_username": creds["username"],  # password not embedded in plan
        "captures": {
            "csv_paths": [],   # AI fills with each downloaded CSV path
            "errors": [],
        },
        "steps": [
            {
                "id": "logout",
                "action": "browser_navigate",
                "args": {"url": LOGOUT_URL},
                "description": "Force logout to ensure clean state for the run",
                "postcondition": f"URL contains '/login'",
            },
            {
                "id": "login_email",
                "action": "browser_type",
                "selectors_hint": [
                    sels["login"]["email_input"]["primary"],
                    sels["login"]["email_input"]["fallback"],
                ],
                "value": creds["username"],
                "description": "Type email into Step 1 of the 2-step login flow",
            },
            {
                "id": "login_continue",
                "action": "browser_click",
                "selectors_hint": [sels["login"]["continue_button"]["primary"]],
                "description": "Advance to password step",
                "postcondition": "Password input is visible (data-testid=login-password-input)",
            },
            {
                "id": "login_password",
                "action": "browser_type",
                "selectors_hint": [
                    sels["login"]["password_input"]["primary"],
                    sels["login"]["password_input"]["fallback"],
                ],
                "value_ref": "creds.password",   # AI fetches via security cmd at runtime; not embedded
                "description": "Type password (Keychain-resolved at runtime)",
            },
            {
                "id": "login_submit",
                "action": "browser_click",
                "selectors_hint": [sels["login"]["signin_button"]["primary"]],
                "description": "Submit login",
                "postcondition": f"URL contains '{sels['login']['post_login_url_match']}'",
            },
            {
                "id": "navigate_sales_summary",
                "action": "browser_navigate",
                "args": {"url": SALES_SUMMARY_URL},
                "description": "Open Sales Summary report",
            },
            {
                "id": "wait_for_load",
                "action": "browser_wait_for",
                "args": {"time": 4},
                "description": "Allow report SPA to mount; selectors only resolve after",
            },
            {
                "id": "ensure_report_type_days",
                "action": "browser_conditional",
                "if": "Report type pill text contains 'Summary' (not 'Days')",
                "then": [
                    {"action": "browser_click", "selectors_hint": [sels["sales_summary"]["report_type_pill"]["primary"]]},
                    {"action": "browser_click", "selectors_hint": [sels["sales_summary"]["report_type_panel"]["days_option_text_only"]]},
                    {"action": "browser_click", "selectors_hint": [sels["sales_summary"]["report_type_panel"]["apply_button"]]},
                    {"action": "browser_wait_for", "args": {"time": 2}},
                ],
                "description": "Switch to Days breakdown if not already; setting is sticky per account",
            },
            {
                "id": "iterate_weeks",
                "action": "python",
                "description": (
                    f"For each (mon, sun) yielded by adapter.iter_weeks({start_date}, {end_date}): "
                    "set the date range via the date_range_pill (TODO: calibrate explicit picker), "
                    "click export_trigger_button, click export_confirm_button, wait for CSV in "
                    "extracted/downloads/, append path to captures.csv_paths."
                ),
                "todo_note": "Date picker calibration pending. v1 workaround: use Previous/Next 7-day buttons.",
            },
            {
                "id": "parse_all",
                "action": "python",
                "description": (
                    "For each path in captures.csv_paths: records.extend(parse_csv(path)). "
                    "Sort by date. Return as the final daily_tips() result."
                ),
            },
        ],
    }


# ── Public entry point ────────────────────────────────────────────


def daily_tips(
    start_date: datetime.date,
    end_date: datetime.date,
    *,
    store: str = "palmetto",
) -> list[dict]:
    """High-level entry point matching adapter.daily_tips() interface.

    v1 (2026-04-19): returns parse_csv() output for the most recent CSV in
    extracted/downloads/ matching the requested date range. Full Playwright
    orchestration happens via the AI agent following build_plan(). When
    Playwright drives the export, it places the CSV in DOWNLOADS_DIR.

    v2 (deferred): direct in-process Playwright via playwright-python so the
    skill is fully self-contained and can run from `python pull_tips.py`
    without an AI in the loop.
    """
    # Find the most recent matching CSV. Naming convention from Square:
    # sales-summary-YYYY-MM-DD-YYYY-MM-DD.csv
    pattern = (
        f"sales-summary-{start_date.year}-{start_date.month:02d}-{start_date.day:02d}-"
        f"{end_date.year}-{end_date.month:02d}-{end_date.day:02d}.csv"
    )
    candidates = list(DOWNLOADS_DIR.glob(pattern))
    if not candidates:
        # Fall back to the broadest sales-summary CSV — useful for debugging.
        candidates = sorted(
            DOWNLOADS_DIR.glob("sales-summary-*.csv"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise FileNotFoundError(
                f"No Sales Summary CSV found matching {pattern} in {DOWNLOADS_DIR}. "
                f"Run build_plan() and have the AI drive Playwright to populate."
            )
    return parse_csv(candidates[0])


# ── CLI ───────────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Square dashboard tip extractor")
    sub = parser.add_subparsers(dest="cmd")

    p_parse = sub.add_parser("parse", help="Parse an existing CSV")
    p_parse.add_argument("csv_path")

    p_plan = sub.add_parser("plan", help="Print the Playwright playbook")
    p_plan.add_argument("--start", required=True, help="YYYY-MM-DD")
    p_plan.add_argument("--end", required=True, help="YYYY-MM-DD")
    p_plan.add_argument("--store", default="palmetto")

    p_creds = sub.add_parser("verify-creds", help="Check Keychain access")
    p_creds.add_argument("--store", default="palmetto")

    args = parser.parse_args()

    if args.cmd == "parse":
        records = parse_csv(pathlib.Path(args.csv_path))
        print(json.dumps(records, indent=2))
    elif args.cmd == "plan":
        plan = build_plan(
            datetime.date.fromisoformat(args.start),
            datetime.date.fromisoformat(args.end),
            store=args.store,
        )
        # Don't leak password even if creds were resolved
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
        parser.print_help()
