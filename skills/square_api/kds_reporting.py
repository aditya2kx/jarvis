#!/usr/bin/env python3
"""skills/square_api/kds_reporting — KDS kitchen metrics via Square Reporting API.

Square's Reporting API (v1) exposes kitchen-display-system (KDS) ticket data via
the ``KDS`` view. This module:

  1. Discovers the KDS view's schema via ``GET /v1/meta`` (run once; result
     committed to agents/bhaga/knowledge-base/research/square-reporting-kds-meta.json).
  2. Queries ticket-level data via ``POST /v1/load`` for a date window.
  3. Synthesizes a ``kds-{start}-{end+1day}.csv`` matching the Playwright-scraped
     column layout (Device Name, Ticket Name, Order Source, Number of Items,
     Items in Ticket, Completion Time (seconds), Time Created, Time Completed,
     Time Due, Time Recalled) so the downstream ``parse_kds_csv`` path is
     unchanged.

DECISION GATE (encoded below in ``_ticket_grain_available``): if the KDS view
exposes ticket-level dimensions, we produce the full CSV; otherwise we emit a
daily-aggregate-only dict (``kds_daily_from_api``) and the caller writes to
``square_kds_daily`` while logging a warning that ``square_kds_tickets`` needs
a fallback scrape.

Reporting API base: https://reporting.squareup.com
Override for testing: SQUARE_REPORTING_BASE env var.
Reference: https://developer.squareup.com/docs/reporting-api/getting-started
"""

from __future__ import annotations

import csv
import datetime
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.square_api.auth import get_access_token
from skills.square_api.client import SQUARE_VERSION

REPORTING_BASE_DEFAULT = "https://reporting.squareup.com"
_META_CACHE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "agents/bhaga/knowledge-base/research/square-reporting-kds-meta.json"
)
_DOWNLOADS_DIR = pathlib.Path(__file__).resolve().parents[2] / "extracted/downloads"

# KDS CSV columns in the exact order produced by the dashboard export.
KDS_CSV_HEADER = [
    "Device Name",
    "Ticket Name",
    "Order Source",
    "Number of Items",
    "Items in Ticket",
    "Completion Time (seconds)",
    "Time Created",
    "Time Completed",
    "Time Due",
    "Time Recalled",
]


def _reporting_base() -> str:
    return os.environ.get("SQUARE_REPORTING_BASE", REPORTING_BASE_DEFAULT).rstrip("/")


def _reporting_request(method: str, path: str, *, token: str, body: dict | None = None) -> dict:
    url = f"{_reporting_base()}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Square-Version", SQUARE_VERSION)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(
            f"Reporting API {method} {path} → HTTP {exc.code}: {detail[:500]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Reporting API {method} {path} failed: {exc}") from exc


def fetch_meta(store: str = "palmetto") -> dict:
    """Fetch /v1/meta and return the full metadata dict."""
    token = get_access_token(store)
    return _reporting_request("GET", "/v1/meta", token=token)


def save_meta_cache(meta: dict) -> None:
    """Commit the raw /v1/meta response to the knowledge-base for future reference."""
    _META_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _META_CACHE.write_text(json.dumps(meta, indent=2))
    print(f"[kds_reporting] wrote Reporting API meta → {_META_CACHE}")


def _find_kds_view(meta: dict) -> dict | None:
    """Return the KDS view entry from the meta response, or None if absent."""
    for view in (meta.get("views") or meta.get("data", {}).get("views") or []):
        if (view.get("name") or "").upper() == "KDS":
            return view
    return None


def _ticket_grain_available(kds_view: dict) -> bool:
    """Return True if the KDS view has ticket-level dimensions.

    We look for any dimension whose name contains 'ticket' (case-insensitive).
    If the API only exposes aggregate measures (daily sums), we fall back to
    the daily-aggregate path and log accordingly.
    """
    dims = kds_view.get("dimensions") or []
    return any("ticket" in (d.get("name") or "").lower() for d in dims)


def _rfc3339_bounds(date: datetime.date, shop_tz: str = "America/Chicago") -> tuple[str, str]:
    """Return (begin_time, end_time) as RFC3339 UTC strings covering one local day."""
    tz = ZoneInfo(shop_tz)
    start_local = datetime.datetime(date.year, date.month, date.day, 0, 0, 0, tzinfo=tz)
    end_local = start_local + datetime.timedelta(days=1)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (
        start_local.astimezone(datetime.timezone.utc).strftime(fmt),
        end_local.astimezone(datetime.timezone.utc).strftime(fmt),
    )


def _query_kds_view(
    *,
    token: str,
    kds_view: dict,
    begin_time: str,
    end_time: str,
) -> list[dict]:
    """POST /v1/load for the KDS view and return all rows as dicts."""
    view_name = kds_view.get("name", "KDS")
    dims = [d["name"] for d in (kds_view.get("dimensions") or [])]
    measures = [m["name"] for m in (kds_view.get("measures") or [])]
    payload: dict = {
        "view": view_name,
        "dimensions": dims,
        "measures": measures,
        "filters": [
            {"dimension": "time_created", "operator": "BETWEEN",
             "value": begin_time, "value2": end_time}
        ],
        "limit": 10000,
    }
    resp = _reporting_request("POST", "/v1/load", token=token, body=payload)
    rows = resp.get("rows") or resp.get("data") or []
    # Normalize: if rows are lists, zip with header names
    if rows and isinstance(rows[0], list):
        header = dims + measures
        return [dict(zip(header, r)) for r in rows]
    return rows


def _build_kds_csv_rows(api_rows: list[dict], shop_tz: str) -> list[list[str]]:
    """Convert Reporting API row dicts to KDS CSV rows (one row = one ticket)."""
    tz = ZoneInfo(shop_tz)

    def _fmt_ts(val: str | None) -> str:
        if not val:
            return ""
        try:
            dt = datetime.datetime.fromisoformat(val.replace("Z", "+00:00"))
            return dt.astimezone(tz).strftime("%m/%d/%Y %I:%M:%S %p")
        except (ValueError, AttributeError):
            return str(val)

    rows: list[list[str]] = []
    for r in api_rows:
        completion_sec = r.get("completion_time_seconds") or r.get("completion_time") or ""
        try:
            completion_sec = str(float(str(completion_sec).replace(",", "")))
        except (ValueError, TypeError):
            completion_sec = ""
        rows.append([
            str(r.get("device_name") or r.get("device") or ""),
            str(r.get("ticket_name") or r.get("ticket") or ""),
            str(r.get("order_source") or ""),
            str(r.get("number_of_items") or r.get("num_items") or ""),
            str(r.get("items_in_ticket") or r.get("items") or ""),
            completion_sec,
            _fmt_ts(r.get("time_created") or r.get("created_at")),
            _fmt_ts(r.get("time_completed") or r.get("completed_at")),
            _fmt_ts(r.get("time_due") or r.get("due_at")),
            _fmt_ts(r.get("time_recalled") or r.get("recalled_at")),
        ])
    return rows


def export_window_kds(
    *,
    start_date: datetime.date,
    end_date: datetime.date,
    store: str = "palmetto",
    shop_tz: str = "America/Chicago",
) -> pathlib.Path | None:
    """Fetch KDS data for [start_date, end_date] and write a synthesized kds CSV.

    Returns the CSV path on success, or None if only daily aggregate data is
    available (in which case the caller should use ``kds_daily_from_api``).

    Decision gate:
    - If ticket-level dimensions exist → full CSV, both square_kds_daily +
      square_kds_tickets populated via the normal parse_kds_csv path.
    - If only aggregates → returns None; caller falls back to weekly scrape for
      ticket grain. This is documented in PROGRESS.md under the WA entry.
    """
    token = get_access_token(store)

    # Load or fetch the meta to determine available dimensions.
    if _META_CACHE.exists():
        try:
            meta = json.loads(_META_CACHE.read_text())
        except Exception:
            meta = fetch_meta(store)
            save_meta_cache(meta)
    else:
        meta = fetch_meta(store)
        save_meta_cache(meta)

    kds_view = _find_kds_view(meta)
    if kds_view is None:
        print(
            "[kds_reporting] WARNING: KDS view not found in Reporting API meta. "
            "Skipping KDS export. square_kds_daily/tickets will not be updated via API."
        )
        return None

    if not _ticket_grain_available(kds_view):
        print(
            "[kds_reporting] WARNING: KDS view has no ticket-level dimensions. "
            "Only daily aggregates available from API. "
            "square_kds_tickets requires weekly Playwright scrape (see PROGRESS.md WA entry)."
        )
        return None

    # Collect rows across the date window
    all_rows: list[list[str]] = []
    current = start_date
    while current <= end_date:
        begin_time, end_time = _rfc3339_bounds(current, shop_tz)
        api_rows = _query_kds_view(
            token=token, kds_view=kds_view, begin_time=begin_time, end_time=end_time
        )
        all_rows.extend(_build_kds_csv_rows(api_rows, shop_tz))
        print(f"[kds_reporting] {current}: {len(api_rows)} KDS tickets fetched")
        current += datetime.timedelta(days=1)

    _DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    end_plus = end_date + datetime.timedelta(days=1)
    kds_path = _DOWNLOADS_DIR / f"kds-{start_date.isoformat()}-{end_plus.isoformat()}.csv"

    with kds_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(KDS_CSV_HEADER)
        for row in all_rows:
            writer.writerow(row)

    print(f"[kds_reporting] wrote {len(all_rows)} KDS rows → {kds_path}")
    return kds_path


if __name__ == "__main__":
    import argparse

    cli = argparse.ArgumentParser(description="Square Reporting API → KDS CSV")
    cli.add_argument("--store", default="palmetto")
    cli.add_argument("--start", required=True, help="YYYY-MM-DD")
    cli.add_argument("--end", required=True, help="YYYY-MM-DD")
    cli.add_argument("--fetch-meta-only", action="store_true",
                     help="Only fetch and print /v1/meta (no CSV output)")
    args = cli.parse_args()

    if args.fetch_meta_only:
        token = get_access_token(args.store)
        meta = fetch_meta(args.store)
        save_meta_cache(meta)
        kds_view = _find_kds_view(meta)
        if kds_view:
            print(f"KDS view found. Ticket grain: {_ticket_grain_available(kds_view)}")
            print(f"Dimensions: {[d['name'] for d in (kds_view.get('dimensions') or [])]}")
            print(f"Measures:   {[m['name'] for m in (kds_view.get('measures') or [])]}")
        else:
            print("KDS view NOT found in Reporting API meta.")
        sys.exit(0)

    result = export_window_kds(
        start_date=datetime.date.fromisoformat(args.start),
        end_date=datetime.date.fromisoformat(args.end),
        store=args.store,
    )
    if result is None:
        print("KDS export returned None (only daily aggregates available or view absent).")
        sys.exit(1)
    print(f"KDS CSV written: {result}")
