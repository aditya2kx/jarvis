"""Daily diff: compare prod vs staging BHAGA model sheets.

Fires at 06:00 CT after both laptop (21:00 CT) and cloud (03:00 CT) runs
have settled. Reads both spreadsheets, computes cell-level diffs, and posts
a summary to the operator's Slack DM.

OBSERVATION ONLY — never writes to any sheet.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests
from google.auth import default as google_auth_default
from google.auth.transport.requests import Request as GoogleAuthRequest
from googleapiclient.discovery import build

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("daily_diff")

CT = ZoneInfo("America/Chicago")

PROD_MODEL_SHEET_ID = "1Drj9nplWcdeRChWQ9fk0dfZQPkQweIuPVL5yqNIDOd0"
STAGING_MODEL_SHEET_ID = "18NH71JwMOAX6euFugSsSQlJhHPgBghWk09YWnsSuvDk"

TABS_TO_COMPARE = ["daily", "labor_daily", "tip_alloc_daily", "review_bonus_period"]

DATE_COLUMN_BY_TAB = {
    "daily": "date",
    "labor_daily": "date",
    "tip_alloc_daily": "date",
    "review_bonus_period": "period_end",
}

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
OPERATOR_SLACK_ID = os.environ.get("OPERATOR_SLACK_ID", "")

MAX_DIFFS_IN_MESSAGE = 5


# ---------------------------------------------------------------------------
# Google Sheets helpers
# ---------------------------------------------------------------------------

def _get_sheets_service():
    creds, _ = google_auth_default(
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    creds.refresh(GoogleAuthRequest())
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def read_tab(service, spreadsheet_id: str, tab_name: str) -> list[dict[str, str]]:
    """Read an entire tab and return list-of-dicts keyed by the header row."""
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"{tab_name}!A:ZZ")
        .execute()
    )
    rows = result.get("values", [])
    if len(rows) < 2:
        return []

    headers = [h.strip().lower() for h in rows[0]]
    records = []
    for row in rows[1:]:
        padded = row + [""] * (len(headers) - len(row))
        records.append({h: padded[i] for i, h in enumerate(headers)})
    return records


# ---------------------------------------------------------------------------
# Diff computation
# ---------------------------------------------------------------------------

def compute_tab_diff(
    tab_name: str,
    prod_rows: list[dict[str, str]],
    staging_rows: list[dict[str, str]],
    target_date: str,
) -> dict[str, Any]:
    """Compare prod vs staging rows for a single tab and target date.

    Returns a dict with row_counts and cell_diffs.
    """
    date_col = DATE_COLUMN_BY_TAB.get(tab_name, "date")

    prod_for_date = [r for r in prod_rows if r.get(date_col) == target_date]
    staging_for_date = [r for r in staging_rows if r.get(date_col) == target_date]

    prod_keyed = _key_rows(prod_for_date, date_col)
    staging_keyed = _key_rows(staging_for_date, date_col)

    cell_diffs: list[dict[str, str]] = []
    all_keys = set(prod_keyed.keys()) | set(staging_keyed.keys())

    for key in sorted(all_keys):
        p_row = prod_keyed.get(key)
        s_row = staging_keyed.get(key)

        if p_row and not s_row:
            cell_diffs.append({
                "tab": tab_name,
                "row_key": key,
                "column": "(entire row)",
                "prod": "present",
                "staging": "MISSING",
            })
            continue
        if s_row and not p_row:
            cell_diffs.append({
                "tab": tab_name,
                "row_key": key,
                "column": "(entire row)",
                "prod": "MISSING",
                "staging": "present",
            })
            continue

        all_cols = set(p_row.keys()) | set(s_row.keys())
        for col in sorted(all_cols):
            pv = _normalize_value(p_row.get(col, ""))
            sv = _normalize_value(s_row.get(col, ""))
            if pv != sv:
                cell_diffs.append({
                    "tab": tab_name,
                    "row_key": key,
                    "column": col,
                    "prod": p_row.get(col, ""),
                    "staging": s_row.get(col, ""),
                })

    return {
        "tab": tab_name,
        "prod_rows": len(prod_for_date),
        "staging_rows": len(staging_for_date),
        "cell_diffs": cell_diffs,
    }


def _key_rows(rows: list[dict[str, str]], date_col: str) -> dict[str, dict[str, str]]:
    """Build a lookup dict keyed by all non-date columns that look like identifiers.

    For daily/labor_daily the key is just the date (one row per date, or
    date+employee). We use ALL column values as a composite key when no
    obvious unique key exists, falling back to row index.
    """
    keyed: dict[str, dict] = {}
    for idx, row in enumerate(rows):
        key_parts = []
        for col in sorted(row.keys()):
            if col in ("", date_col):
                continue
            val = row.get(col, "")
            if _looks_like_identifier(col):
                key_parts.append(f"{col}={val}")
        if not key_parts:
            key_parts.append(f"_idx={idx}")
        key = "|".join(key_parts)
        keyed[key] = row
    return keyed


def _looks_like_identifier(col: str) -> bool:
    id_hints = ("name", "employee", "period", "team_member")
    return any(hint in col.lower() for hint in id_hints)


def _normalize_value(v: str) -> str:
    """Normalize a cell value for comparison (strip whitespace, unify numbers)."""
    v = v.strip()
    try:
        return f"{float(v):.6f}"
    except (ValueError, TypeError):
        return v


# ---------------------------------------------------------------------------
# Slack messaging
# ---------------------------------------------------------------------------

def get_operator_dm_channel(token: str, user_id: str) -> str:
    """Open a DM channel with the operator and return the channel ID."""
    resp = requests.post(
        "https://slack.com/api/conversations.open",
        headers={"Authorization": f"Bearer {token}"},
        json={"users": user_id},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"conversations.open failed: {data.get('error')}")
    return data["channel"]["id"]


def format_diff_message(target_date: str, tab_results: list[dict[str, Any]]) -> str:
    """Build the Slack message text from diff results."""
    total_diffs = sum(len(r["cell_diffs"]) for r in tab_results)

    lines: list[str] = []

    if total_diffs == 0:
        lines.append(f":white_check_mark: Cloud matches laptop for *{target_date}*")
    else:
        lines.append(f":warning: *{total_diffs} diff(s)* for *{target_date}*")

    lines.append("")
    for r in tab_results:
        status = ":white_check_mark:" if not r["cell_diffs"] else f":warning: {len(r['cell_diffs'])} diff(s)"
        lines.append(
            f"*{r['tab']}*: prod={r['prod_rows']} rows, staging={r['staging_rows']} rows — {status}"
        )

    if total_diffs > 0:
        lines.append("")
        lines.append("*Top differences:*")
        all_diffs = []
        for r in tab_results:
            all_diffs.extend(r["cell_diffs"])

        for d in all_diffs[:MAX_DIFFS_IN_MESSAGE]:
            lines.append(
                f"  • `{d['tab']}` | key=`{d['row_key'][:40]}` | col=`{d['column']}` | "
                f"prod=`{d['prod'][:30]}` → staging=`{d['staging'][:30]}`"
            )

        remaining = total_diffs - MAX_DIFFS_IN_MESSAGE
        if remaining > 0:
            lines.append(f"  … and {remaining} more")

    return "\n".join(lines)


def post_slack_dm(token: str, channel: str, text: str) -> None:
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {token}"},
        json={"channel": channel, "text": text},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"chat.postMessage failed: {data.get('error')}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    now_ct = datetime.now(CT)
    yesterday_ct = now_ct - timedelta(days=1)
    target_date = os.environ.get("DIFF_DATE", yesterday_ct.strftime("%Y-%m-%d"))

    log.info("Daily diff starting for target_date=%s", target_date)

    prod_sheet = os.environ.get("PROD_MODEL_SHEET_ID", PROD_MODEL_SHEET_ID)
    staging_sheet = os.environ.get("STAGING_MODEL_SHEET_ID", STAGING_MODEL_SHEET_ID)

    try:
        service = _get_sheets_service()
    except Exception:
        log.exception("Failed to authenticate with Google Sheets API")
        return 1

    tab_results: list[dict[str, Any]] = []
    for tab in TABS_TO_COMPARE:
        log.info("Comparing tab: %s", tab)
        try:
            prod_rows = read_tab(service, prod_sheet, tab)
            staging_rows = read_tab(service, staging_sheet, tab)
        except Exception:
            log.exception("Failed to read tab %s", tab)
            return 1

        result = compute_tab_diff(tab, prod_rows, staging_rows, target_date)
        tab_results.append(result)
        log.info(
            "  %s: prod=%d staging=%d diffs=%d",
            tab, result["prod_rows"], result["staging_rows"], len(result["cell_diffs"]),
        )

    message = format_diff_message(target_date, tab_results)
    log.info("Diff message:\n%s", message)

    token = SLACK_BOT_TOKEN
    if not token:
        log.error("SLACK_BOT_TOKEN not set — cannot post to Slack")
        return 1

    operator_id = OPERATOR_SLACK_ID
    if not operator_id:
        log.error("OPERATOR_SLACK_ID not set — cannot determine DM channel")
        return 1

    try:
        dm_channel = get_operator_dm_channel(token, operator_id)
        post_slack_dm(token, dm_channel, message)
        log.info("Posted diff summary to Slack DM channel=%s", dm_channel)
    except Exception:
        log.exception("Failed to post Slack DM")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
