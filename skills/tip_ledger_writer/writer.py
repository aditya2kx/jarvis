#!/usr/bin/env python3
"""skills/tip_ledger_writer/writer - Idempotent writes into the three BHAGA workbooks.

Each public function writes one tab in one workbook, applying upsert semantics
keyed by the tab's `natural_key_columns` (defined in schema.py). Rows that
don't match any incoming natural key are PRESERVED — this is critical for
backfill + incremental refresh coexisting in the same sheet.

API summary:

    write_raw_adp_shifts(spreadsheet_id, shifts, *, account="palmetto")
    write_raw_adp_punches(spreadsheet_id, punches, *, account="palmetto")
    write_raw_adp_rates(spreadsheet_id, rates, *, account="palmetto")
    write_raw_square_transactions(spreadsheet_id, txns, *, account="palmetto")
    write_raw_square_daily_rollup(spreadsheet_id, rollups, *, account="palmetto")

All five share a single `_upsert_tab()` core that:

    1. Reads the existing tab (whole sheet).
    2. Validates the header row matches the schema (else raises -- caller must
       re-bootstrap or migrate; we won't silently overwrite a different
       layout).
    3. Builds an index of existing rows by natural key.
    4. Overlays incoming records (new entries added; matching keys replaced).
    5. Sorts deterministically (by natural key) and writes back via
       values.update for the data range, then optionally clears trailing rows
       if the new dataset shrunk.

Stamps every written row with `scraped_at_utc` (ISO-8601 with Z suffix).

Failure modes:
    * Header drift: ValueError with the diff. M3 orchestrator must alert via slack.
    * Missing column in record: the cell is written as empty string (defensive
      -- we don't want one bad punch breaking the whole batch).
    * Network/quota: bubbles up the underlying HTTPError with body.

Performance notes (calibrated 2026-05-16):
    * Reading 2956 transaction rows: ~1s.
    * Writing 2956 transaction rows (19 cols): ~3-5s.
    * Google Sheets API quota: 60 read + 60 write per user per minute. The
      orchestrator's daily refresh stays well under this.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable, Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from core.config_loader import refresh_access_token
from skills.tip_ledger_writer.schema import WORKBOOK_SCHEMAS, get_tab_spec


SHEETS_API = "https://sheets.googleapis.com/v4"


# ── Low-level API helpers ─────────────────────────────────────────


def _api(url: str, token: str, *, method: str = "GET", data: dict | None = None) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode(errors="replace")
        raise RuntimeError(f"{method} {url} -> HTTP {e.code}\n{err_body}") from None


def _read_tab(spreadsheet_id: str, tab: str, token: str) -> list[list[Any]]:
    """Return the full sheet as a list of rows (list[Any] per row). Empty trailing
    rows/cols are omitted by the API."""
    range_a1 = urllib.parse.quote(f"{tab}!A:ZZ", safe="")
    url = f"{SHEETS_API}/spreadsheets/{spreadsheet_id}/values/{range_a1}"
    resp = _api(url, token)
    return resp.get("values", [])


def _write_range(
    spreadsheet_id: str,
    range_a1: str,
    values: list[list[Any]],
    token: str,
    *,
    value_input_option: str = "RAW",
) -> dict:
    """Write a 2D array of values to a range. Caller controls the range bounds."""
    enc_range = urllib.parse.quote(range_a1, safe="")
    url = (
        f"{SHEETS_API}/spreadsheets/{spreadsheet_id}/values/{enc_range}"
        f"?valueInputOption={value_input_option}"
    )
    return _api(url, token, method="PUT", data={"values": values})


def _clear_range(spreadsheet_id: str, range_a1: str, token: str) -> dict:
    enc_range = urllib.parse.quote(range_a1, safe="")
    url = f"{SHEETS_API}/spreadsheets/{spreadsheet_id}/values/{enc_range}:clear"
    return _api(url, token, method="POST", data={})


def _col_letter(n: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA. n is 1-indexed."""
    out = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


# ── Schema-aware row encoding ─────────────────────────────────────


# Columns whose Python value is a list/dict and should be JSON-encoded on write.
_JSON_COLUMNS = {
    "rate_history_json",
    "raw_employee_names_json",
    "employee_aliases_json",
}


def _encode_cell(value: Any) -> Any:
    """Convert a Python value to the cell representation the Sheets API expects."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return value  # Sheets renders as TRUE/FALSE
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, (list, dict)):
        return json.dumps(value, default=str)
    return str(value)


def _record_to_row(record: dict, header: list[str], scraped_at_utc: str) -> list[Any]:
    """Map a record dict to a row matching the header order.

    Special handling:
        * `scraped_at_utc` is injected from the parameter (overrides any value
          in record so the whole batch has a single coherent stamp).
        * Columns in `_JSON_COLUMNS` get JSON-encoded from the source field
          named without the `_json` suffix (e.g. `rate_history_json` reads
          from `record["rate_history"]`).
        * Missing keys become empty cells.
    """
    row = []
    for col in header:
        if col == "scraped_at_utc":
            row.append(scraped_at_utc)
            continue
        if col in _JSON_COLUMNS:
            src_key = col[:-len("_json")] if col.endswith("_json") else col
            value = record.get(src_key, record.get(col, None))
            row.append(_encode_cell(value))
            continue
        row.append(_encode_cell(record.get(col)))
    return row


def _row_natural_key(row: list[Any], header: list[str], key_cols: tuple) -> tuple:
    """Extract the natural-key tuple from an existing row, using header positions."""
    out = []
    for col in key_cols:
        try:
            idx = header.index(col)
        except ValueError:
            raise ValueError(
                f"Schema mismatch: natural-key column {col!r} not in header {header!r}"
            )
        cell = row[idx] if idx < len(row) else ""
        out.append(_normalize_key_cell(cell))
    return tuple(out)


def _record_natural_key(record: dict, key_cols: tuple) -> tuple:
    return tuple(_normalize_key_cell(record.get(c)) for c in key_cols)


def _normalize_key_cell(v: Any) -> str:
    """Both 5 and '5' and 5.0 must compare equal when used as a key. Use string form."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


# ── Core upsert ───────────────────────────────────────────────────


def _upsert_tab(
    spreadsheet_id: str,
    workbook_title: str,
    tab_name: str,
    records: Iterable[dict],
    *,
    account: str,
    scraped_at_utc: Optional[str] = None,
) -> dict:
    """Read tab, overlay records by natural key, write back. Returns a summary dict.

    Summary keys:
        existing_rows, incoming_records, inserted, updated, total_after, tab
    """
    spec = get_tab_spec(workbook_title, tab_name)
    header_expected = spec["header"]
    key_cols = spec["natural_key_columns"]
    if scraped_at_utc is None:
        scraped_at_utc = (
            datetime.datetime.now(datetime.timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )

    token = refresh_access_token(account=account)

    raw = _read_tab(spreadsheet_id, tab_name, token)
    if not raw:
        raise RuntimeError(
            f"Tab '{tab_name}' in spreadsheet {spreadsheet_id} appears empty. "
            f"Bootstrap may not have run — expected header row at row 1."
        )

    header_actual = list(raw[0])
    # Truncate trailing notes/sidecar columns (bootstrap puts a freeform note 2
    # columns past the header); the data columns are the first len(header_expected).
    header_actual_data = header_actual[: len(header_expected)]
    if header_actual_data != header_expected:
        diff = [
            (i, exp, got)
            for i, (exp, got) in enumerate(zip(header_expected, header_actual_data))
            if exp != got
        ]
        raise ValueError(
            f"Header drift on tab '{tab_name}' (workbook '{workbook_title}', "
            f"spreadsheet {spreadsheet_id}). Diffs (col_idx, expected, actual): {diff}. "
            f"Re-run bootstrap_sheets.py or migrate the tab before retrying."
        )

    # Build index of existing data rows by natural key.
    existing_data_rows = raw[1:]
    by_key: dict[tuple, list[Any]] = {}
    for r in existing_data_rows:
        # Skip fully-blank rows (they can sneak in from manual editing).
        if not any(str(c).strip() for c in r):
            continue
        try:
            k = _row_natural_key(r, header_expected, key_cols)
        except ValueError:
            continue
        if not any(k):
            continue
        # Pad/truncate to header width.
        padded = list(r[: len(header_expected)]) + [""] * (len(header_expected) - len(r))
        by_key[k] = padded

    existing_count = len(by_key)

    inserted, updated = 0, 0
    incoming = list(records)
    for rec in incoming:
        k = _record_natural_key(rec, key_cols)
        if not any(k):
            raise ValueError(
                f"Record missing natural-key values {key_cols} for tab '{tab_name}': {rec!r}"
            )
        row = _record_to_row(rec, header_expected, scraped_at_utc)
        if k in by_key:
            updated += 1
        else:
            inserted += 1
        by_key[k] = row

    # Sort deterministically by natural key tuple.
    sorted_keys = sorted(by_key.keys())
    sorted_rows = [by_key[k] for k in sorted_keys]

    # Write back: header is unchanged, data starts at row 2.
    if sorted_rows:
        last_col = _col_letter(len(header_expected))
        data_range = f"{tab_name}!A2:{last_col}{1 + len(sorted_rows)}"
        _write_range(spreadsheet_id, data_range, sorted_rows, token)

    # If old count > new count, clear the trailing rows so they don't linger.
    if len(existing_data_rows) > len(sorted_rows):
        trailing_start = 2 + len(sorted_rows)
        trailing_end = 1 + len(existing_data_rows)
        last_col = _col_letter(len(header_expected))
        _clear_range(
            spreadsheet_id,
            f"{tab_name}!A{trailing_start}:{last_col}{trailing_end}",
            token,
        )

    return {
        "workbook": workbook_title,
        "tab": tab_name,
        "spreadsheet_id": spreadsheet_id,
        "existing_rows": existing_count,
        "incoming_records": len(incoming),
        "inserted": inserted,
        "updated": updated,
        "total_after": len(sorted_rows),
        "scraped_at_utc": scraped_at_utc,
    }


# ── Public write functions ────────────────────────────────────────


def write_raw_adp_shifts(
    spreadsheet_id: str,
    shifts: list[dict],
    *,
    account: str = "palmetto",
    scraped_at_utc: Optional[str] = None,
) -> dict:
    """Idempotent upsert into BHAGA ADP Raw > shifts. Natural key: (date, employee_id)."""
    return _upsert_tab(
        spreadsheet_id, "BHAGA ADP Raw", "shifts", shifts,
        account=account, scraped_at_utc=scraped_at_utc,
    )


def write_raw_adp_punches(
    spreadsheet_id: str,
    punches: list[dict],
    *,
    account: str = "palmetto",
    scraped_at_utc: Optional[str] = None,
) -> dict:
    """Idempotent upsert into BHAGA ADP Raw > punches. Natural key:
    (date, employee_id, punch_idx_in_day)."""
    return _upsert_tab(
        spreadsheet_id, "BHAGA ADP Raw", "punches", punches,
        account=account, scraped_at_utc=scraped_at_utc,
    )


def write_raw_adp_rates(
    spreadsheet_id: str,
    rates: list[dict],
    *,
    account: str = "palmetto",
    scraped_at_utc: Optional[str] = None,
) -> dict:
    """Idempotent upsert into BHAGA ADP Raw > wage_rates. Natural key: (employee_id,).

    Source records come from compensation_backend.compensation(); the writer
    JSON-encodes rate_history -> rate_history_json and raw_employee_names ->
    raw_employee_names_json automatically.
    """
    return _upsert_tab(
        spreadsheet_id, "BHAGA ADP Raw", "wage_rates", rates,
        account=account, scraped_at_utc=scraped_at_utc,
    )


def write_raw_square_transactions(
    spreadsheet_id: str,
    transactions: list[dict],
    *,
    account: str = "palmetto",
    scraped_at_utc: Optional[str] = None,
) -> dict:
    """Idempotent upsert into BHAGA Square Raw > transactions. Natural key:
    (transaction_id,)."""
    return _upsert_tab(
        spreadsheet_id, "BHAGA Square Raw", "transactions", transactions,
        account=account, scraped_at_utc=scraped_at_utc,
    )


def write_raw_square_daily_rollup(
    spreadsheet_id: str,
    rollups: list[dict],
    *,
    account: str = "palmetto",
    scraped_at_utc: Optional[str] = None,
) -> dict:
    """Idempotent upsert into BHAGA Square Raw > daily_rollup. Natural key:
    (date_local,)."""
    return _upsert_tab(
        spreadsheet_id, "BHAGA Square Raw", "daily_rollup", rollups,
        account=account, scraped_at_utc=scraped_at_utc,
    )


# ── CLI ───────────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse

    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest="cmd")

    p_info = sub.add_parser("info", help="Print the schema for all workbooks.")

    for tab, fn_name in [
        ("adp_shifts", "write_raw_adp_shifts"),
        ("adp_punches", "write_raw_adp_punches"),
        ("adp_rates", "write_raw_adp_rates"),
        ("square_transactions", "write_raw_square_transactions"),
        ("square_daily_rollup", "write_raw_square_daily_rollup"),
    ]:
        p = sub.add_parser(tab, help=f"Run {fn_name}() with records from a JSON file.")
        p.add_argument("--spreadsheet-id", required=True)
        p.add_argument("--records-json", required=True,
            help="Path to a JSON file containing a list[dict] of records.")
        p.add_argument("--account", default="palmetto")

    args = cli.parse_args()

    if args.cmd == "info":
        print(json.dumps(
            {wb: [{"tab": s["tab_name"], "key": list(s["natural_key_columns"]),
                   "header": s["header"]} for s in tabs]
             for wb, tabs in WORKBOOK_SCHEMAS.items()},
            indent=2,
        ))
    elif args.cmd:
        records = json.loads(open(args.records_json).read())
        fn = {
            "adp_shifts": write_raw_adp_shifts,
            "adp_punches": write_raw_adp_punches,
            "adp_rates": write_raw_adp_rates,
            "square_transactions": write_raw_square_transactions,
            "square_daily_rollup": write_raw_square_daily_rollup,
        }[args.cmd]
        summary = fn(args.spreadsheet_id, records, account=args.account)
        print(json.dumps(summary, indent=2))
    else:
        cli.print_help()
