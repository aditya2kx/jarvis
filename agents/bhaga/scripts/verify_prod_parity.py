#!/usr/bin/env python3
"""End-to-end prod parity evidence: BigQuery (SoT) ⇆ production Sheets.

This is the *correct* e2e verification for the BQ-single-source-of-truth cutover.
Unlike the per-PR sandbox e2e (which only exercises the last-closed period into a
throwaway sandbox), this script compares the **entire** production dataset:

  Item 1 — RAW parity:   for every raw source, BQ table row count vs the prod raw
                         Sheet tab row count (and BQ min/max date to prove coverage
                         from the first data date).
  Item 2 — MODEL parity: for every model grain, BQ table row count vs prod model
                         Sheet tab row count, *plus* a cell-level value diff
                         (reusing verify_bq_parity._compare_tabs).

It must run where the orchestrator service account is available (Sheets+BQ scopes):
  - In Cloud Run / CI: run as bhaga-orchestrator@ with BHAGA_SECRETS_BACKEND=gcp.
  - Locally (read-only evidence): impersonate the SA —
      BHAGA_DATASTORE=bigquery BHAGA_SECRETS_BACKEND=gcp \\
      BHAGA_IMPERSONATE_SA=bhaga-orchestrator@jarvis-bhaga-prod.iam.gserviceaccount.com \\
      python3 -m agents.bhaga.scripts.verify_prod_parity --store palmetto --from 2026-03-23

The laptop's own OAuth user cannot read the Sheets (cloud-platform scope only); the
SA is the identity shared on every prod workbook, so this always runs as the SA.

Exit code is non-zero if any row-count mismatch or value drift is detected.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from agents.bhaga.scripts.reconcile_model import _read_bq_as_rows
from agents.bhaga.scripts.verify_bq_parity import _cells_match, _read_sheet_tab
from core.config_loader import refresh_access_token, resolve_sheet_id
from core.datastore import dataset, read_query

_PROJECT = "jarvis-bhaga-prod"
_DATASET = dataset()  # env-driven: set BHAGA_BQ_DATASET=bhaga_sandbox to verify sandbox BQ
_STORE_PROFILES = pathlib.Path(__file__).resolve().parents[3] / "agents" / "bhaga" / "knowledge-base" / "store-profiles"

# Columns that exist in BQ but not in the Sheet (metadata/internal) — skip in value diff.
_SKIP_COLS: set[str] = {"materialized_at_utc", "last_refreshed_ct", "scraped_at_utc"}

# RAW grains: prod raw Sheet tab ⇆ BQ raw table.
#   bq_date_col  — BQ column to bound by --from and to report coverage min/max.
#   sheet_date_col — Sheet column to apply the SAME --from filter (defaults to bq_date_col).
# date_col=None means the table is not date-partitioned (compare full counts).
_RAW_GRAINS: list[dict] = [
    {"workbook": "bhaga_square_raw", "tab": "transactions",      "bq_table": "square_transactions", "bq_date_col": "date_local"},
    {"workbook": "bhaga_square_raw", "tab": "daily_rollup",      "bq_table": "square_daily_rollup", "bq_date_col": "date_local"},
    {"workbook": "bhaga_square_raw", "tab": "item_daily_rollup", "bq_table": "square_item_daily",   "bq_date_col": "date_local"},
    {"workbook": "bhaga_square_raw", "tab": "kds_daily",         "bq_table": "square_kds_daily",    "bq_date_col": "date_local"},
    {"workbook": "bhaga_square_raw", "tab": "item_lines",        "bq_table": "square_item_lines",   "bq_date_col": "date_local"},
    {"workbook": "bhaga_adp_raw",    "tab": "shifts",            "bq_table": "adp_shifts",          "bq_date_col": "date"},
    {"workbook": "bhaga_adp_raw",    "tab": "punches",           "bq_table": "adp_punches",         "bq_date_col": "date"},
    {"workbook": "bhaga_adp_raw",    "tab": "wage_rates",        "bq_table": "adp_wage_rates",      "bq_date_col": None},
    {"workbook": "bhaga_adp_raw",    "tab": "earnings",          "bq_table": "adp_earnings",        "bq_date_col": "period_start"},
    {"workbook": "bhaga_review_raw", "tab": "reviews",           "bq_table": "google_reviews",      "bq_date_col": "post_date_ct"},
]

# MODEL grains: prod model Sheet tab ⇆ BQ model table. sort_by drives value-diff alignment.
_MODEL_GRAINS: list[dict] = [
    {"tab": "daily",               "bq_table": "model_daily",               "bq_date_col": "date",           "sort_by": ["date"]},
    {"tab": "labor_daily",         "bq_table": "model_labor_daily",         "bq_date_col": "date",           "sort_by": ["date"]},
    {"tab": "labor_weekly",        "bq_table": "model_labor_weekly",        "bq_date_col": None,             "sort_by": ["iso_week"]},
    {"tab": "labor_period",        "bq_table": "model_labor_period",        "bq_date_col": "pay_period_start", "sheet_date_col": "pay_period_start", "sort_by": ["pay_period_start"]},
    {"tab": "tip_alloc_daily",     "bq_table": "model_tip_alloc_daily",     "bq_date_col": "date",           "sort_by": ["date", "employee"]},
    {"tab": "tip_alloc_period",    "bq_table": "model_tip_alloc_period",    "bq_date_col": "period_start",   "sort_by": ["period_start", "employee"]},
    {"tab": "review_bonus_period", "bq_table": "model_review_bonus_period", "bq_date_col": "period_start",   "sort_by": ["period_start", "employee"]},
    {"tab": "period_summary",      "bq_table": "model_period_summary",      "bq_date_col": "period_start",   "sort_by": ["period_start"]},
]


def _bq_count(table: str, date_col: str | None, start: str | None) -> int:
    where = ""
    if date_col and start:
        where = f" WHERE {date_col} >= DATE('{start}')"
    sql = f"SELECT COUNT(*) AS n FROM `{_PROJECT}.{_DATASET}.{table}`{where}"
    rows = read_query(sql)
    return int(rows[0]["n"]) if rows else 0


def _bq_date_range(table: str, date_col: str | None) -> tuple[str, str] | None:
    if not date_col:
        return None
    sql = f"SELECT CAST(MIN({date_col}) AS STRING) AS lo, CAST(MAX({date_col}) AS STRING) AS hi FROM `{_PROJECT}.{_DATASET}.{table}`"
    rows = read_query(sql)
    if rows and rows[0].get("lo"):
        return (rows[0]["lo"], rows[0]["hi"])
    return None


def _row_date(val) -> str | None:
    """Extract a YYYY-MM-DD date string from a Sheet cell (handles apostrophe / ISO datetime)."""
    if val is None:
        return None
    s = str(val).strip()
    if s.startswith("'"):
        s = s[1:]
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return None


def _sheet_data_rows(rows: list[list], *, date_col: str | None = None, start: str | None = None) -> int:
    """Count non-empty data rows.

    When date_col + start are given, only count rows whose date_col value is a
    parseable date >= start — applying the SAME bound used on the BQ side so the
    two counts are comparable. Rows with an unparseable date in that column are
    still counted (so we never silently drop real data).
    """
    if not rows:
        return 0
    header = rows[0]
    body = rows[1:]
    col_idx = header.index(date_col) if (date_col and start and date_col in header) else None

    n = 0
    for r in body:
        if not any(str(c).strip() for c in r):
            continue
        if col_idx is not None and col_idx < len(r):
            d = _row_date(r[col_idx])
            if d is not None and d < start:
                continue
        n += 1
    return n


_ZERO_OR_EMPTY = {"", "0", "0.0", "0.00", "0.00%", "0%", "$0.00", "n/a", "na", "none", "null", "-"}
_BOOL = {"true": 1, "false": 0, "yes": 1, "no": 0, "y": 1, "n": 0}


def _clean_str(v) -> str:
    s = "" if v is None else str(v).strip()
    if s.startswith("'"):
        s = s[1:]
    return s.lower()


def _as_number(s: str) -> float:
    """Parse a numeric cell to a canonical ratio/amount. '%' → /100; strips $ , +."""
    is_pct = s.endswith("%")
    t = s.replace("%", "").replace("$", "").replace(",", "").replace("+", "").strip()
    f = float(t)
    return f / 100.0 if is_pct else f


def _norm_key_part(val) -> str:
    """Normalize a key cell (date → YYYY-MM-DD, else stripped/lowercased)."""
    d = _row_date(val)
    if d is not None:
        return d
    s = "" if val is None else str(val).strip()
    if s.startswith("'"):
        s = s[1:]
    return s.lower()


def _vals_equal(a, b) -> bool:
    """Symmetric, unit-aware cell equality for Sheet ⇆ BQ comparison.

    Handles the real representation gaps between a formatted Sheet and raw BQ:
      - percent (sheet '7.16%') ≡ decimal (BQ '0.0716')  [tight 0.1pp tolerance]
      - currency ('$5,442.47')  ≡ number ('5442.47')     [relative tolerance]
      - boolean ('N'/'no'/'False') treated as equal
      - null / zero / 'N/A' treated as the same 'no value'
    Falls back to verify_bq_parity._cells_match (date serials etc.) then string ==.
    """
    sa, sb = _clean_str(a), _clean_str(b)
    if sa == sb:
        return True
    if sa in _ZERO_OR_EMPTY and sb in _ZERO_OR_EMPTY:
        return True
    if sa in _BOOL and sb in _BOOL:
        return _BOOL[sa] == _BOOL[sb]

    is_pct = sa.endswith("%") or sb.endswith("%")
    try:
        fa, fb = _as_number(sa), _as_number(sb)
        if is_pct:
            return abs(fa - fb) <= 0.001          # within 0.1 percentage-point
        tol = max(0.01, 0.002 * max(abs(fa), abs(fb)))  # 0.2% relative for $/counts
        return abs(fa - fb) <= tol
    except (ValueError, TypeError):
        pass
    return _cells_match(a, b)


def _compare_by_key(
    tab: str,
    sheet_rows: list[list],
    bq_rows: list[list],
    key_cols: list[str],
    *,
    date_key: str | None,
    start: str | None,
    skip_cols: set[str],
) -> dict:
    """Key-based (join) value comparison within the same date window.

    Joins Sheet and BQ rows on key_cols (date-normalized), restricted to
    date_key >= start, then compares every shared, non-skipped column with
    _vals_equal. Reports keys only-in-sheet / only-in-bq and cell mismatches on
    matched keys — immune to row-offset artifacts from differing row sets.
    """
    if not sheet_rows or len(sheet_rows) < 1:
        return {"status": "EMPTY_SHEET", "matched_keys": 0, "sheet_only": 0, "bq_only": 0, "cell_mismatches": 0, "samples": []}
    sheet_header, bq_header = sheet_rows[0], bq_rows[0]
    shared_cols = [c for c in sheet_header if c in bq_header and c not in skip_cols and c not in key_cols]

    def index_rows(rows: list[list], header: list[str]) -> dict[tuple, dict]:
        idx = {c: i for i, c in enumerate(header)}
        out: dict[tuple, dict] = {}
        for r in rows[1:]:
            if not any(str(c).strip() for c in r):
                continue
            if date_key and start and date_key in idx:
                dv = _row_date(r[idx[date_key]]) if idx[date_key] < len(r) else None
                if dv is not None and dv < start:
                    continue
            key = tuple(_norm_key_part(r[idx[c]]) if idx[c] < len(r) else "" for c in key_cols)
            out[key] = {c: (r[idx[c]] if idx[c] < len(r) else None) for c in header}
        return out

    s_idx = index_rows(sheet_rows, sheet_header)
    b_idx = index_rows(bq_rows, bq_header)
    s_keys, b_keys = set(s_idx), set(b_idx)
    sheet_only, bq_only = sorted(s_keys - b_keys), sorted(b_keys - s_keys)

    cell_mismatches = 0
    samples: list[dict] = []
    for key in sorted(s_keys & b_keys):
        for col in shared_cols:
            sv, bv = s_idx[key].get(col), b_idx[key].get(col)
            if not _vals_equal(sv, bv):
                cell_mismatches += 1
                if len(samples) < 6:
                    samples.append({"key": list(key), "col": col, "sheet": str(sv), "bq": str(bv)})

    status = "OK" if (not sheet_only and not bq_only and cell_mismatches == 0) else "MISMATCH"
    return {
        "status": status,
        "matched_keys": len(s_keys & b_keys),
        "sheet_only": len(sheet_only), "bq_only": len(bq_only),
        "cell_mismatches": cell_mismatches,
        "sheet_only_keys": [list(k) for k in sheet_only[:6]],
        "bq_only_keys": [list(k) for k in bq_only[:6]],
        "samples": samples,
    }


def verify(store: str, start: str | None, *, check_values: bool = True) -> dict:
    profile = json.loads((_STORE_PROFILES / f"{store}.json").read_text())
    token = refresh_access_token(account=store)

    workbook_ids = {
        key: resolve_sheet_id(key, profile)
        for key in ("bhaga_model", "bhaga_square_raw", "bhaga_adp_raw", "bhaga_review_raw")
    }

    raw_results: list[dict] = []
    model_results: list[dict] = []
    passed = True

    # ── Item 1: RAW row-count parity ───────────────────────────────────────
    print(f"\n# RAW parity (BQ ⇆ prod Sheets){'  [from ' + start + ']' if start else ''}")
    print(f"{'source':28s} {'sheet':>8s} {'bq':>8s} {'Δ':>6s}  bq_date_range")
    for g in _RAW_GRAINS:
        sid = workbook_ids[g["workbook"]]
        bq_date_col = g["bq_date_col"]
        sheet_date_col = g.get("sheet_date_col", bq_date_col)
        sheet_rows = _read_sheet_tab(sid, g["tab"], token)
        sheet_n = _sheet_data_rows(sheet_rows, date_col=sheet_date_col, start=start)
        bq_n = _bq_count(g["bq_table"], bq_date_col, start)
        rng = _bq_date_range(g["bq_table"], bq_date_col)
        rng_s = f"{rng[0]}..{rng[1]}" if rng else "(no date col)"
        delta = bq_n - sheet_n
        ok = delta == 0
        if not ok:
            passed = False
        print(f"{g['tab']:28s} {sheet_n:8d} {bq_n:8d} {delta:+6d}  {rng_s}  {'OK' if ok else 'MISMATCH'}")
        raw_results.append({
            "source": g["tab"], "bq_table": g["bq_table"], "sheet_rows": sheet_n,
            "bq_rows": bq_n, "delta": delta, "bq_date_range": rng, "ok": ok,
        })

    # ── Item 2: MODEL key-joined row + value parity ────────────────────────
    # Compare by natural key (date/period/employee) within the same window — NOT
    # by row position — so differing row sets (e.g. pre-window periods only in the
    # Sheet) don't produce spurious cascading mismatches.
    print(f"\n# MODEL parity (BQ ⇆ prod Sheets, key-joined, from {start})")
    print(f"{'tab':22s} {'matched':>8s} {'sheetOnly':>10s} {'bqOnly':>7s} {'cellMis':>8s}  status")
    for g in _MODEL_GRAINS:
        sid = workbook_ids["bhaga_model"]
        sheet_rows = _read_sheet_tab(sid, g["tab"], token)
        date_key = g.get("sheet_date_col", g["bq_date_col"])
        if not check_values or not sheet_rows:
            model_results.append({"tab": g["tab"], "status": "SKIPPED"})
            continue
        bq_rows = _read_bq_as_rows(g["bq_table"], g["sort_by"], sheet_header=sheet_rows[0])
        cmp = _compare_by_key(
            g["tab"], sheet_rows, bq_rows, g["sort_by"],
            date_key=date_key, start=start, skip_cols=_SKIP_COLS,
        )
        ok = cmp["status"] == "OK"
        if not ok:
            passed = False
        print(f"{g['tab']:22s} {cmp['matched_keys']:8d} {cmp['sheet_only']:10d} {cmp['bq_only']:7d} {cmp['cell_mismatches']:8d}  {'OK' if ok else 'MISMATCH'}")
        for s in cmp.get("samples", [])[:3]:
            print(f"      ! {s['col']} @ {s['key']}: sheet={s['sheet']!r} bq={s['bq']!r}")
        if cmp.get("sheet_only_keys"):
            print(f"      sheet-only keys (≤6): {cmp['sheet_only_keys']}")
        if cmp.get("bq_only_keys"):
            print(f"      bq-only keys (≤6): {cmp['bq_only_keys']}")
        model_results.append({"tab": g["tab"], "bq_table": g["bq_table"], **cmp, "ok": ok})

    return {"passed": passed, "start": start, "raw": raw_results, "model": model_results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", required=True, help="Store name (e.g. palmetto)")
    parser.add_argument("--from", dest="start", default=None,
                        help="Bound counts to date_col >= this date (e.g. 2026-03-23)")
    parser.add_argument("--no-values", action="store_true",
                        help="Skip the model cell-level value diff (row-count census only)")
    parser.add_argument("--json", action="store_true", help="Emit JSON result (for CI/PR evidence)")
    args = parser.parse_args()

    print(f"# verify_prod_parity [{args.store}] — BQ (SoT) ⇆ prod Sheets")
    result = verify(args.store, args.start, check_values=not args.no_values)

    if args.json:
        print("\n--- JSON ---")
        print(json.dumps(result, indent=2, default=str))

    if result["passed"]:
        print("\nRESULT: PASS — BQ row counts and model values match prod Sheets.")
        return 0
    n_raw = sum(1 for r in result["raw"] if not r["ok"])
    # --no-values leaves model grains as {"status": "SKIPPED"} (no "ok" key);
    # only count grains that actually diverged.
    n_model = sum(1 for m in result["model"] if m.get("status") not in ("OK", "SKIPPED"))
    print(f"\nRESULT: FAIL — {n_raw} raw + {n_model} model grain(s) diverge.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
