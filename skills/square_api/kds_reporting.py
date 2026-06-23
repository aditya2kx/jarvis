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

from skills.square_api.auth import get_access_token, SQUARE_VERSION

REPORTING_BASE_DEFAULT = "https://connect.squareup.com/reporting"
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
    """Return the KDS cube/view entry from the meta response, or None if absent.

    The Reporting API returns a ``cubes`` list (not ``views``) at the top level.
    We prefer the ``KDS`` cube which includes both ticket and item dimensions.
    """
    candidates = (
        meta.get("cubes")
        or meta.get("views")
        or meta.get("data", {}).get("cubes")
        or meta.get("data", {}).get("views")
        or []
    )
    for entry in candidates:
        if (entry.get("name") or "").upper() == "KDS":
            return entry
    return None


def _ticket_grain_available(kds_view: dict) -> bool:
    """Return True if the KDS cube has ticket-level dimensions.

    The Reporting API KDS cube exposes ticket_name, chit_created_at,
    actual_completed_at, line_item_count — all ticket-grain fields.
    We look for any dimension whose name contains 'ticket' (case-insensitive).
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
    """POST /v1/load for the KDS cube and return all rows as dicts.

    The Reporting API uses Cube.js query format: measures + timeDimensions +
    optional dimensions. We select the ticket-grain dimensions needed to
    reconstruct ``square_kds_tickets`` and ``square_kds_daily``.
    """
    cube = kds_view.get("name", "KDS")
    # Ticket-grain dimensions sufficient to reconstruct the browser-scraped columns:
    # Device Name, Ticket Name, Order Source, Number of Items,
    # Completion Time, Time Created, Time Completed, Time Due
    dims = [
        f"{cube}.ticket_name",
        f"{cube}.order_source",
        f"{cube}.line_item_count",
        f"{cube}.display_on_kds_at",   # "Time Created" in the dashboard CSV
        f"{cube}.chit_created_at",     # order creation time (used as fallback only)
        f"{cube}.actual_completed_at",
        f"{cube}.time_due",
        f"{cube}.device_code_name",
    ]
    measures = [
        f"{cube}.ticket_count",
        f"{cube}.avg_ticket_time_seconds",
        f"{cube}.median_ticket_time_seconds",
    ]
    # Use only dims available on this cube
    available_dims = {d["name"] for d in (kds_view.get("dimensions") or [])}
    available_meas = {m["name"] for m in (kds_view.get("measures") or [])}
    dims = [d for d in dims if d in available_dims]
    measures = [m for m in measures if m in available_meas]

    # Filter by display_on_kds_at (the dashboard's "Time Created" = when ticket appeared
    # on the KDS screen). Fall back to chit_created_at if display_on_kds_at is not available.
    time_dim = (
        f"{cube}.display_on_kds_at"
        if f"{cube}.display_on_kds_at" in available_dims
        else f"{cube}.chit_created_at"
    )
    payload: dict = {
        "query": {
            "dimensions": dims,
            "measures": measures,
            "timeDimensions": [{
                "dimension": time_dim,
                "dateRange": [begin_time, end_time],
            }],
            "limit": 50000,
        }
    }
    resp = _reporting_request("POST", "/v1/load", token=token, body=payload)
    rows = resp.get("data") or resp.get("rows") or []
    # Normalize: if rows are lists, zip with header names
    if rows and isinstance(rows[0], list):
        header = dims + measures
        return [dict(zip(header, r)) for r in rows]
    return rows


def _build_kds_csv_rows(api_rows: list[dict], shop_tz: str) -> list[list[str]]:
    """Convert Reporting API row dicts to KDS CSV rows (one row = one ticket).

    The Reporting API returns keys prefixed with the cube name (e.g. "KDS.ticket_name").
    We strip that prefix and map to the KDS CSV column layout.
    """
    tz = ZoneInfo(shop_tz)

    def _fmt_ts(val: str | None) -> str:
        if not val:
            return ""
        try:
            dt = datetime.datetime.fromisoformat(val.replace("Z", "+00:00"))
            # Reporting API returns UTC timestamps without a Z suffix; treat as UTC.
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt.astimezone(tz).strftime("%m/%d/%Y %I:%M:%S %p")
        except (ValueError, AttributeError):
            return str(val)

    def _get(r: dict, *keys: str) -> str:
        """Fetch first matching key, stripping 'CUBE.' prefix from API keys."""
        stripped = {k.split(".", 1)[-1]: v for k, v in r.items()}
        for key in keys:
            v = r.get(key) or stripped.get(key)
            if v is not None:
                return str(v)
        return ""

    # Deduplicate: including time_due as a Cube.js dimension can produce multiple
    # rows for the same ticket when items within a ticket have different time_due
    # values (some null, some set). Group by ticket natural key and keep the row
    # that has a non-null time_due; fall back to any row if none has time_due.
    deduped: dict[tuple, dict] = {}
    for r in api_rows:
        s = {k.split(".", 1)[-1]: v for k, v in r.items()}
        key = (
            s.get("ticket_name") or "",
            s.get("display_on_kds_at") or s.get("chit_created_at") or "",
            s.get("device_code_name") or s.get("device_name") or "",
        )
        existing = deduped.get(key)
        if existing is None or (not (existing.get("time_due") or existing.get("KDS.time_due"))
                                and (s.get("time_due") or s.get("KDS.time_due"))):
            deduped[key] = r

    rows: list[list[str]] = []
    for r in deduped.values():
        stripped = {k.split(".", 1)[-1]: v for k, v in r.items()}
        # "Time Created" in the dashboard CSV = display_on_kds_at (when the ticket
        # appeared on the KDS screen), NOT chit_created_at (order creation time).
        # Use chit_created_at only as a fallback when display_on_kds_at is absent.
        display_ts = stripped.get("display_on_kds_at") or stripped.get("chit_created_at") or ""
        completed_ts = stripped.get("actual_completed_at") or ""
        order_src = str(stripped.get("order_source") or "").lower()
        # The Square dashboard CSV does not include time_due for Kiosk orders even
        # though the Reporting API populates it (Kiosk auto-assigns an internal due
        # time; it is not a customer-scheduled pickup). Clear it to match the CSV.
        if "kiosk" in order_src:
            time_due_ts = ""
        else:
            time_due_ts = stripped.get("time_due") or ""

        # Completion time = seconds from KDS display to actual completion.
        # Both timestamps are UTC without a Z suffix.
        if display_ts and completed_ts:
            try:
                t0 = datetime.datetime.fromisoformat(display_ts.replace("Z", "+00:00"))
                t1 = datetime.datetime.fromisoformat(completed_ts.replace("Z", "+00:00"))
                # Both naive (UTC) — delta is correct.
                completion_sec = str(round(max(0.0, (t1 - t0).total_seconds())))
            except (ValueError, AttributeError):
                completion_sec = ""
        else:
            completion_sec = ""

        num_items = stripped.get("line_item_count") or ""
        try:
            num_items = str(int(float(str(num_items))))
        except (ValueError, TypeError):
            num_items = str(num_items)

        rows.append([
            str(stripped.get("device_code_name") or stripped.get("device_name") or ""),
            str(stripped.get("ticket_name") or ""),
            str(stripped.get("order_source") or ""),
            num_items,
            "",  # "Items in Ticket" — not available at ticket grain; left blank
            completion_sec,
            _fmt_ts(display_ts),
            _fmt_ts(completed_ts),
            _fmt_ts(time_due_ts),
            _fmt_ts(stripped.get("time_recalled") or ""),
        ])
    return rows


def ingest_window_kds(
    *,
    start_date: datetime.date,
    end_date: datetime.date,
    store: str = "palmetto",
    shop_tz: str = "America/Chicago",
) -> dict[str, int]:
    """Fetch KDS data for [start_date, end_date] and load BigQuery directly.

    Queries the Reporting API KDS cube at ticket grain, passes the result
    through ``parse_kds_dictrows`` (same calibrated parser as the scrape path),
    then aggregates via ``aggregate_daily_kds_stats`` and maps to BQ via
    ``map_square_kds_daily`` / ``map_kds_ticket``. No CSV is written.

    Returns row counts: {"square_kds_daily": N, "square_kds_tickets": N}.
    Returns {} if KDS cube is absent or ticket grain is unavailable.
    """
    from agents.bhaga.scripts.backfill_bigquery import map_square_kds_daily, map_kds_ticket
    from agents.bhaga.scripts.backfill_from_downloads import _TS_TYPES
    from skills.square_tips import transactions_backend as tb
    from core.datastore import load_rows

    token = get_access_token(store)

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
        print("[kds_reporting] KDS cube not found — skipping KDS API load")
        return {}
    if not _ticket_grain_available(kds_view):
        print("[kds_reporting] ticket grain unavailable — skipping KDS API load")
        return {}

    all_csv_rows: list[list[str]] = []
    cur = start_date
    while cur <= end_date:
        b, e = _rfc3339_bounds(cur, shop_tz)
        api_rows = _query_kds_view(token=token, kds_view=kds_view, begin_time=b, end_time=e)
        all_csv_rows += _build_kds_csv_rows(api_rows, shop_tz)
        print(f"[kds_reporting] {cur}: {len(api_rows)} KDS tickets fetched")
        cur += datetime.timedelta(days=1)

    dict_rows = [dict(zip(KDS_CSV_HEADER, r)) for r in all_csv_rows]
    tickets = tb.parse_kds_dictrows(dict_rows, shop_tz=shop_tz)
    tickets = [t for t in tickets
               if start_date.isoformat() <= t["date_local"] <= end_date.isoformat()]

    daily_stats = tb.aggregate_daily_kds_stats(tickets)
    daily = [{"date_local": d, **s} for d, s in sorted(daily_stats.items())]

    counts: dict[str, int] = {}

    bq_daily = [map_square_kds_daily(r) for r in daily if r.get("date_local")]
    counts["square_kds_daily"] = load_rows(
        "square_kds_daily", bq_daily,
        merge_keys=["date_local"],
        column_bq_types=_TS_TYPES,
    )

    bq_tix = [map_kds_ticket(r) for r in tickets]
    bq_tix = [r for r in bq_tix if r.get("date_local")]
    counts["square_kds_tickets"] = load_rows(
        "square_kds_tickets", bq_tix,
        merge_keys=["date_local", "time_created", "ticket_name"],
        column_bq_types=_TS_TYPES,
    )

    print(f"[kds_reporting] BQ row counts: {counts}")
    return counts


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
