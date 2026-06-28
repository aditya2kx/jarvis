#!/usr/bin/env python3
"""One-time migration: snapshot Google Sheets human inputs into BQ.

Reads the CURRENT production Sheet data (using the legacy Sheet readers
that still exist on this branch) and writes them into BQ so the new
BQ-canonical path has data before the Sheet readers are retired.

Idempotent: uses MERGE semantics — safe to re-run.

Prerequisites:
    - Migration 020_sheet_inputs.sql applied (run ensure_schema() first)
    - ADC-authenticated with access to prod BQ + Google Sheets
    - BHAGA_SECRETS_BACKEND=gcp BHAGA_DATASTORE=bigquery

Run:
    BHAGA_SECRETS_BACKEND=gcp BHAGA_DATASTORE=bigquery \\
        python3 -m agents.bhaga.scripts.migrate_inputs_to_bq \\
        [--store palmetto] [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from core.datastore import ensure_schema, load_rows, read_query, fq
from core.store_config import get_all, set_config
from core.config_loader import refresh_access_token, resolve_sheet_id


STORE_PROFILES = (
    pathlib.Path(__file__).resolve().parents[2]
    / "bhaga"
    / "knowledge-base"
    / "store-profiles"
)


def _today_central() -> datetime.date:
    """Return today's date in America/Chicago (Central Time).

    Isolated so tests can patch it without fighting the datetime module.
    """
    from zoneinfo import ZoneInfo  # noqa: PLC0415
    return datetime.datetime.now(ZoneInfo("America/Chicago")).date()


def _load_profile(store: str) -> dict:
    return json.loads((STORE_PROFILES / f"{store}.json").read_text())


def migrate_training_shifts(
    profile: dict,
    store: str,
    *,
    dry_run: bool,
    open_period_only: bool = True,
) -> int:
    """Read training_shifts Sheet tab -> BQ training_shifts table.

    When ``open_period_only=True`` (the default), only rows whose date falls
    within the current open pay period are ingested.  Rows dated in a closed/
    already-paid period are skipped and reported so the operator can decide
    whether a backfill is warranted.  This prevents a late training-shift entry
    from silently restating a closed period's ``our_calc``.

    Pass ``--allow-closed-periods`` on the CLI (or ``open_period_only=False``)
    to ingest all rows regardless of period status.
    """
    model_sid = resolve_sheet_id("bhaga_model", profile)
    import urllib.parse
    import urllib.request

    token = refresh_access_token(store)
    rng = urllib.parse.quote("training_shifts!A1:C500", safe="!:")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{model_sid}/values/{rng}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    import urllib.error
    out: set[tuple[str, str]] = set()
    notes_map: dict[tuple[str, str], str] = {}
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            import json as _j
            data = _j.loads(resp.read())
        from skills.bhaga_config.dates import coerce_iso_date  # noqa: PLC0415
        for row in data.get("values", [])[1:]:  # skip header
            if not row or not row[0].strip():
                continue
            name = row[0].strip()
            if len(row) < 2 or not row[1].strip():
                continue
            raw_date = row[1].strip()
            # Try ISO first; fall back to coerce_iso_date (handles M/D/YYYY etc.)
            iso = None
            try:
                iso = datetime.date.fromisoformat(raw_date).isoformat()
            except ValueError:
                iso = coerce_iso_date(raw_date)
            if iso is None:
                # Last-resort: try M/D/YYYY manually
                parts = raw_date.replace("/", "-").split("-")
                if len(parts) == 3:
                    try:
                        m, d, y = int(parts[0]), int(parts[1]), int(parts[2])
                        iso = datetime.date(y, m, d).isoformat()
                    except (ValueError, TypeError):
                        pass
            if iso is None:
                print(f"  [migrate] WARN: unparseable date for {name!r}: {raw_date!r}")
                continue
            date_iso = iso
            out.add((name, date_iso))
            notes_map[(name, date_iso)] = row[2].strip() if len(row) > 2 else ""
    except urllib.error.HTTPError as exc:
        if exc.code == 400:
            print(f"  [migrate] training_shifts tab not found in sheet — skipping")
            return 0
        raise

    print(f"  training_shifts: {len(out)} row(s) from Sheet")
    if not out:
        return 0

    # ── Open-period filter (default-on guard) ──────────────────────────────
    # Prevents a Sheet entry dated in a closed/already-paid period from
    # restating that period's our_calc.  Only rows in the current open period
    # (i.e. date > most-recent closed period end) are ingested.
    if open_period_only:
        from agents.bhaga.scripts.update_model_sheet import (  # noqa: PLC0415
            most_recent_closed_period,
        )
        adp_cfg = profile.get("adp_run", {})
        anchor = adp_cfg.get("pay_periods_anchor_end_date", "")
        freq = adp_cfg.get("pay_frequency", "Biweekly")
        if anchor:
            _, closed_end = most_recent_closed_period(
                anchor_end_date=anchor,
                pay_frequency=freq,
                today=_today_central(),
            )
            open_start_iso = (
                closed_end + datetime.timedelta(days=1)
            ).isoformat()
            in_open: set[tuple[str, str]] = set()
            skipped: list[tuple[str, str]] = []
            for name, date_iso in out:
                if date_iso >= open_start_iso:
                    in_open.add((name, date_iso))
                else:
                    skipped.append((name, date_iso))
            for name, date_iso in sorted(skipped):
                print(
                    f"  [migrate] SKIP closed-period: {name!r} {date_iso}"
                    f" (open period starts {open_start_iso})"
                )
            if skipped:
                print(
                    f"  [migrate] {len(skipped)} row(s) skipped (closed period); "
                    f"{len(in_open)} eligible for open period"
                )
            out = in_open
        else:
            raise RuntimeError(
                f"open_period_only=True but store profile for {store!r} has no "
                f"pay_periods_anchor_end_date under adp_run. "
                f"Either add the anchor date to the store profile or pass "
                f"--allow-closed-periods to disable the guard."
            )

    if not out:
        print("  training_shifts: 0 rows to ingest after open-period filter")
        return 0

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    rows = [
        {
            "store": store,
            "employee_name": name,
            "date": date_iso,
            "note": notes_map.get((name, date_iso), ""),
            "updated_at": now_iso,
            "updated_by": "migrate",
        }
        for name, date_iso in sorted(out)
    ]
    if dry_run:
        print(f"  DRY-RUN: would MERGE {len(rows)} training_shift rows into BQ")
        for r in rows:
            print(f"    {r['employee_name']} {r['date']}")
        return len(rows)
    load_rows(
        "training_shifts", rows,
        merge_keys=["store", "employee_name", "date"],
        column_bq_types={"date": "DATE", "updated_at": "TIMESTAMP"},
    )
    print(f"  training_shifts: wrote {len(rows)} rows to BQ")
    return len(rows)


def migrate_config_keys(profile: dict, store: str, *, dry_run: bool) -> int:
    """Read bhaga_model > config Sheet tab -> store_config BQ + update BQ keys."""
    from skills.store_profile.reader import _read_config_tab as _sheet_cfg  # noqa: PLC0415
    from agents.bhaga.scripts.update_model_sheet import (  # noqa: PLC0415
        REVIEW_TUNABLE_KEYS, LABOR_TUNABLE_KEYS,
    )

    cfg = _sheet_cfg(store)  # {key: {"value": v, "notes": n}}
    # data_window_end is DERIVED (MAX(square_transactions.date_local)), not a human tunable.
    # Do NOT migrate it — readers use the MAX() fallback when the key is absent in store_config,
    # which stays live every nightly run. Migrating it would freeze the value and cause drift.
    interesting_keys = set(REVIEW_TUNABLE_KEYS) | set(LABOR_TUNABLE_KEYS) | {
        "excluded_from_tip_pool",
    }
    # Also pick up any training_excluded:* keys.
    for key in list(cfg.keys()):
        if key.startswith("training_excluded:"):
            interesting_keys.add(key)

    written = 0
    for key in sorted(interesting_keys):
        rec = cfg.get(key)
        if rec is None:
            continue
        val = (rec.get("value") or "").strip()
        notes = (rec.get("notes") or "").strip()
        if not val:
            continue
        if dry_run:
            print(f"  DRY-RUN: store_config set {key!r} = {val!r}")
        else:
            set_config(store, key, val, updated_by="migrate", notes=notes)
        written += 1

    print(f"  store_config: migrated {written} key(s)")
    return written


def migrate_employee_aliases(profile: dict, store: str, *, dry_run: bool) -> int:
    """Read bhaga_model > employees Sheet tab -> BQ employee_aliases table."""
    from skills.store_profile.reader import _read_employees_tab  # noqa: PLC0415

    roster = _read_employees_tab(store)  # list[dict] with canonical_name, aliases_list, ...
    if not roster:
        print("  [migrate] employees tab empty or unreadable — skipping aliases")
        return 0

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    rows = []
    for rec in roster:
        canonical = (rec.get("canonical_name") or "").strip()
        if not canonical:
            continue
        all_raw = [canonical] + rec.get("aliases_list", [])
        for raw in all_raw:
            raw = raw.strip()
            if not raw:
                continue
            rows.append({
                "store": store,
                "raw_name": raw,
                "canonical_name": canonical,
                "notes": "",
                "updated_at": now_iso,
                "updated_by": "migrate",
            })

    print(f"  employee_aliases: {len(rows)} row(s) from Sheet")
    if not rows:
        return 0

    if dry_run:
        print(f"  DRY-RUN: would MERGE {len(rows)} employee_alias rows into BQ")
        for r in rows:
            print(f"    {r['raw_name']!r} -> {r['canonical_name']!r}")
        return len(rows)

    load_rows(
        "employee_aliases", rows,
        merge_keys=["store", "raw_name"],
        column_bq_types={"updated_at": "TIMESTAMP"},
    )
    print(f"  employee_aliases: wrote {len(rows)} rows to BQ")
    return len(rows)


def verify(store: str) -> None:
    """Print row counts from BQ for verification."""
    for table in ("training_shifts", "employee_aliases"):
        rows = read_query(f"SELECT COUNT(*) AS c FROM {fq(table)} WHERE store='{store}'")
        count = rows[0]["c"] if rows else 0
        print(f"  {table} (store={store}): {count} row(s) in BQ")

    existing_cfg = get_all(store)
    training_keys = {k for k in existing_cfg if k.startswith("training_excluded:")}
    print(f"  store_config training_excluded: {len(training_keys)} key(s) in BQ")


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(
        description="One-time migration of Sheet human inputs into BQ.",
    )
    cli.add_argument("--store", default="palmetto")
    cli.add_argument("--dry-run", action="store_true")
    cli.add_argument(
        "--skip-schema", action="store_true",
        help="Skip ensure_schema() (useful if migration 020 is already applied)",
    )
    cli.add_argument(
        "--allow-closed-periods", action="store_true",
        help=(
            "Disable the default open-period-only guard and ingest training-shift rows "
            "regardless of whether their date falls in a closed/already-paid pay period. "
            "Use only for explicit historical backfills; the default (guard on) prevents "
            "silent restatement of closed-period our_calc values."
        ),
    )
    args = cli.parse_args(argv)

    print(f"=== migrate_inputs_to_bq store={args.store} dry_run={args.dry_run} ===")

    if not args.skip_schema:
        print("\n[Step 0] Applying pending BQ migrations...")
        applied = ensure_schema()
        print(f"  applied: {applied or 'none (already up to date)'}")

    profile = _load_profile(args.store)
    print(f"\n[Step 1] Migrating training_shifts...")
    migrate_training_shifts(
        profile, args.store,
        dry_run=args.dry_run,
        open_period_only=not args.allow_closed_periods,
    )

    print(f"\n[Step 2] Migrating config/tunable keys to store_config BQ...")
    migrate_config_keys(profile, args.store, dry_run=args.dry_run)

    print(f"\n[Step 3] Migrating employee aliases to employee_aliases BQ table...")
    migrate_employee_aliases(profile, args.store, dry_run=args.dry_run)

    print(f"\n[Verify]")
    if not args.dry_run:
        verify(args.store)
    else:
        print("  (dry-run — skipping BQ row count verification)")

    print("\n=== Done ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
