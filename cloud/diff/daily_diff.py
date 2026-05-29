"""Daily parity diff: compare ALL prod vs staging BHAGA sheets.

Fires at 22:00 CT after both laptop (21:00 CT) and cloud (21:30 CT) runs
have settled. Reads all 4 spreadsheet pairs × their tabs, computes
cell-level diffs (ignoring cosmetic timestamp columns), and posts a
summary to the operator's Slack DM.

OBSERVATION ONLY — never writes to any sheet.

Spreadsheet pairs (prod → staging):
    bhaga_model       — 6+ tabs (daily, labor_daily, tip_alloc_daily, …)
    bhaga_adp_raw     — 3 tabs (shifts, punches, wage_rates)
    bhaga_square_raw  — 2 tabs (transactions, daily_rollup)
    bhaga_review_raw  — 2 tabs (reviews, unparseable)
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

# ── Sheet pairs: prod and staging spreadsheet IDs ──
# Each pair has env-var overrides and hardcoded defaults.

SHEET_PAIRS: list[dict[str, str]] = [
    {
        "label": "Model",
        "prod_env": "PROD_MODEL_SID",
        "staging_env": "STAGING_MODEL_SID",
        "prod_default": "1Drj9nplWcdeRChWQ9fk0dfZQPkQweIuPVL5yqNIDOd0",
        "staging_default": "18NH71JwMOAX6euFugSsSQlJhHPgBghWk09YWnsSuvDk",
    },
    {
        "label": "ADP Raw",
        "prod_env": "PROD_ADP_RAW_SID",
        "staging_env": "STAGING_ADP_RAW_SID",
        "prod_default": "1-08EIN6EO72t-ImCKRCf4gbIaVN5cJ1FRVlekccvg6w",
        "staging_default": "1sv-zK6Mc_ybPUZrObt0CWmodxIVNYm3ahfZg8WZtLyo",
    },
    {
        "label": "Square Raw",
        "prod_env": "PROD_SQUARE_RAW_SID",
        "staging_env": "STAGING_SQUARE_RAW_SID",
        "prod_default": "1q_uP14ZvbxPBLy8HcgK0EmwaQMmIPP1jwTV3xmd6kZU",
        "staging_default": "1X2sCGwJi8YfcM0DAYlDzHBxG3_Du4jLauppfAw_A1rw",
    },
    {
        "label": "Review Raw",
        "prod_env": "PROD_REVIEW_RAW_SID",
        "staging_env": "STAGING_REVIEW_RAW_SID",
        "prod_default": "1FRtLNy5Ae-m7TK-Q0-alA62A-F7l0cwRZLj1sUMBfmM",
        "staging_default": "16pkNefCOEcEUlhIU6zH03nEcg5PXmBpJhkHy3aUa-k4",
    },
]

IGNORED_TABS = frozenset({"config", "labor_daily_forecast"})

IGNORED_COLUMNS = frozenset({
    "scraped_at_utc",
    "scraped_at",
    "last_refreshed_ct",
    "hours_per_order",
    "avg_order_price",
    "avg_net_sales_plus_tips_per_order",
    "items_sold",
    "avg_items_per_order",
    "hours_per_item",
    "avg_item_price",
    "hourly_hours_per_order",
    "fulltime_hours_per_order",
    "hourly_hours_per_item",
    "fulltime_hours_per_item",
    "kds_completed_tickets",
    "kds_completed_items",
    "kds_median_time_per_item_sec",
    "kds_p90_time_per_item_sec",
    "kds_p95_time_per_item_sec",
    "kds_p99_time_per_item_sec",
    "kds_pct_items_over_goal",
    "kds_pct_tickets_late",
    # Informational annotations (human-readable WHY for outlier_flag /
    # forecast_exclude). Free text derived from per-store robust-z stats, so
    # exact wording can differ between prod and staging without being a real
    # parity regression.
    "outlier_reason",
    "forecast_exclude_reason",
})

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
OPERATOR_SLACK_ID = os.environ.get("OPERATOR_SLACK_ID", "")
MAX_DIFFS_IN_MESSAGE = 8


# ---------------------------------------------------------------------------
# Google Sheets helpers
# ---------------------------------------------------------------------------

def _get_sheets_service():
    creds, _ = google_auth_default(
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    creds.refresh(GoogleAuthRequest())
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def list_tabs(service, spreadsheet_id: str) -> list[str]:
    """Return all visible tab names for a spreadsheet."""
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets.properties.title,sheets.properties.hidden",
    ).execute()
    return [
        s["properties"]["title"]
        for s in meta.get("sheets", [])
        if not s["properties"].get("hidden", False)
    ]


def read_tab(service, spreadsheet_id: str, tab_name: str) -> list[list[str]]:
    """Read an entire tab and return raw rows (list of lists, first row = headers)."""
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"{tab_name}!A:ZZ")
        .execute()
    )
    return result.get("values", [])


def read_tab_as_dicts(service, spreadsheet_id: str, tab_name: str) -> list[dict[str, str]]:
    """Read a tab and return list-of-dicts keyed by the header row."""
    rows = read_tab(service, spreadsheet_id, tab_name)
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

def _normalize_value(v: str) -> str:
    """Normalize a cell value for comparison."""
    v = v.strip()
    try:
        return f"{float(v):.6f}"
    except (ValueError, TypeError):
        return v


def compute_tab_diff(
    sheet_label: str,
    tab_name: str,
    prod_rows: list[dict[str, str]],
    staging_rows: list[dict[str, str]],
) -> dict[str, Any]:
    """Compare prod vs staging rows for a single tab.

    Uses all non-ignored columns as a composite key to match rows.
    Returns a dict with row counts and cell-level diffs.
    """
    all_cols = set()
    for r in prod_rows:
        all_cols.update(r.keys())
    for r in staging_rows:
        all_cols.update(r.keys())
    compare_cols = sorted(all_cols - IGNORED_COLUMNS - {""})

    prod_keyed = _key_rows(prod_rows, compare_cols)
    staging_keyed = _key_rows(staging_rows, compare_cols)

    cell_diffs: list[dict[str, str]] = []
    all_keys = set(prod_keyed.keys()) | set(staging_keyed.keys())

    for key in sorted(all_keys):
        p_row = prod_keyed.get(key)
        s_row = staging_keyed.get(key)

        if p_row and not s_row:
            cell_diffs.append({
                "sheet": sheet_label,
                "tab": tab_name,
                "row_key": key,
                "column": "(entire row)",
                "prod": "present",
                "staging": "MISSING",
            })
            continue
        if s_row and not p_row:
            cell_diffs.append({
                "sheet": sheet_label,
                "tab": tab_name,
                "row_key": key,
                "column": "(entire row)",
                "prod": "MISSING",
                "staging": "present",
            })
            continue

        for col in compare_cols:
            pv = _normalize_value(p_row.get(col, ""))
            sv = _normalize_value(s_row.get(col, ""))
            if pv != sv:
                cell_diffs.append({
                    "sheet": sheet_label,
                    "tab": tab_name,
                    "row_key": key,
                    "column": col,
                    "prod": p_row.get(col, ""),
                    "staging": s_row.get(col, ""),
                })

    return {
        "sheet": sheet_label,
        "tab": tab_name,
        "prod_rows": len(prod_rows),
        "staging_rows": len(staging_rows),
        "cell_diffs": cell_diffs,
    }


def _key_rows(
    rows: list[dict[str, str]], compare_cols: list[str],
) -> dict[str, dict[str, str]]:
    """Build a lookup dict using identity/key-like columns, falling back to index."""
    keyed: dict[str, dict] = {}
    id_cols = [c for c in compare_cols if _looks_like_key(c)]
    for idx, row in enumerate(rows):
        if id_cols:
            key = "|".join(f"{c}={row.get(c, '')}" for c in id_cols)
        else:
            key = f"_idx={idx}"
        keyed[key] = row
    return keyed


def _looks_like_key(col: str) -> bool:
    hints = (
        "name", "employee", "period", "team_member", "date", "dow",
        "hour", "transaction_id", "review_id", "employee_id",
        "punch_idx", "pay_period",
    )
    return any(hint in col.lower() for hint in hints)


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


def format_diff_message(
    run_date: str,
    all_results: list[dict[str, Any]],
) -> str:
    """Build the Slack message text from diff results across all sheets."""
    total_diffs = sum(len(r["cell_diffs"]) for r in all_results)
    total_tabs = len(all_results)
    clean_tabs = sum(1 for r in all_results if not r["cell_diffs"])

    lines: list[str] = []

    if total_diffs == 0:
        lines.append(
            f":white_check_mark: *BHAGA Parity Check — {run_date}*\n"
            f"Cloud matches laptop across all {total_tabs} tabs."
        )
    else:
        lines.append(
            f":warning: *BHAGA Parity Check — {run_date}*\n"
            f"*{total_diffs} diff(s)* across {total_tabs - clean_tabs} tab(s) "
            f"({clean_tabs}/{total_tabs} clean)."
        )

    current_sheet = None
    for r in all_results:
        if r["sheet"] != current_sheet:
            current_sheet = r["sheet"]
            lines.append(f"\n*{current_sheet}*")
        n_diffs = len(r["cell_diffs"])
        if n_diffs == 0:
            status = ":white_check_mark:"
        else:
            status = f":warning: {n_diffs} diff(s)"
        lines.append(
            f"  `{r['tab']}`: prod={r['prod_rows']} rows, "
            f"staging={r['staging_rows']} rows — {status}"
        )

    if total_diffs > 0:
        lines.append("\n*Top differences:*")
        all_diffs = []
        for r in all_results:
            all_diffs.extend(r["cell_diffs"])

        for d in all_diffs[:MAX_DIFFS_IN_MESSAGE]:
            lines.append(
                f"  • `{d['sheet']}` > `{d['tab']}` | "
                f"key=`{d['row_key'][:40]}` | col=`{d['column']}` | "
                f"prod=`{d['prod'][:30]}` → staging=`{d['staging'][:30]}`"
            )

        remaining = total_diffs - MAX_DIFFS_IN_MESSAGE
        if remaining > 0:
            lines.append(f"  … and {remaining} more")

    lines.append(f"\n_Laptop 21:00 CT → Cloud 21:30 CT → Diff 22:00 CT_")
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
    run_date = os.environ.get("DIFF_DATE", now_ct.strftime("%Y-%m-%d"))

    log.info("Daily parity diff starting for run_date=%s", run_date)

    try:
        service = _get_sheets_service()
    except Exception:
        log.exception("Failed to authenticate with Google Sheets API")
        return 1

    all_results: list[dict[str, Any]] = []

    for pair in SHEET_PAIRS:
        prod_sid = os.environ.get(pair["prod_env"], pair["prod_default"])
        staging_sid = os.environ.get(pair["staging_env"], pair["staging_default"])
        label = pair["label"]

        log.info("── %s: prod=%s staging=%s", label, prod_sid[:12], staging_sid[:12])

        try:
            prod_tabs = set(list_tabs(service, prod_sid))
            staging_tabs = set(list_tabs(service, staging_sid))
        except Exception:
            log.exception("Failed to list tabs for %s", label)
            return 1

        common_tabs = sorted(
            (prod_tabs & staging_tabs) - IGNORED_TABS,
        )

        prod_only = prod_tabs - staging_tabs - IGNORED_TABS
        staging_only = staging_tabs - prod_tabs - IGNORED_TABS

        if prod_only:
            log.warning("  tabs only in prod: %s", prod_only)
            for t in sorted(prod_only):
                all_results.append({
                    "sheet": label, "tab": t,
                    "prod_rows": "?", "staging_rows": 0,
                    "cell_diffs": [{
                        "sheet": label, "tab": t, "row_key": "-",
                        "column": "(tab)", "prod": "exists", "staging": "MISSING",
                    }],
                })

        if staging_only:
            log.warning("  tabs only in staging: %s", staging_only)
            for t in sorted(staging_only):
                all_results.append({
                    "sheet": label, "tab": t,
                    "prod_rows": 0, "staging_rows": "?",
                    "cell_diffs": [{
                        "sheet": label, "tab": t, "row_key": "-",
                        "column": "(tab)", "prod": "MISSING", "staging": "exists",
                    }],
                })

        for tab in common_tabs:
            log.info("  comparing tab: %s", tab)
            try:
                prod_rows = read_tab_as_dicts(service, prod_sid, tab)
                staging_rows = read_tab_as_dicts(service, staging_sid, tab)
            except Exception:
                log.exception("  failed to read tab %s", tab)
                return 1

            result = compute_tab_diff(label, tab, prod_rows, staging_rows)
            all_results.append(result)
            log.info(
                "    prod=%d staging=%d diffs=%d",
                result["prod_rows"], result["staging_rows"],
                len(result["cell_diffs"]),
            )

    message = format_diff_message(run_date, all_results)
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
