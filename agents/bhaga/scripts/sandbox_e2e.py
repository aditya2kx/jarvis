#!/usr/bin/env python3
"""Zero-OTP, prod-like e2e for the BHAGA sales / labor / tip / model core.

Provisions ephemeral sandbox sheets, replays scrape artifacts from the GCS
cache (read-only) into the sandbox RAW sheets, builds the MODEL into the sandbox,
asserts the model tabs are populated, prints evidence, then tears the sandbox
down. Runs against isolated sandbox sheets — never the production workbooks.

STRUCTURAL no-OTP guarantee
---------------------------
This runner composes ONLY downstream replay code:

    gcs_cache.download_cached_files      (read-only GCS restore)
    backfill_from_downloads.main         (pure parsers -> sandbox RAW sheets)
    update_model_sheet.main              (sandbox RAW -> sandbox MODEL)

It never imports or invokes any Square / ADP / ClickUp scrape or login module,
so it can never launch a browser or trigger an OTP. ``test_sandbox_e2e_no_otp``
enforces this by importing this module in an isolated interpreter and asserting
no scrape/login module is present in ``sys.modules``.

Reviews are intentionally out of scope (they need a live ClickUp call); the e2e
proves the sales/labor/tip/model core. Item-level operations are picked up
automatically if/when ``backfill_item_lines_from_cache`` lands on main.

Usage:
    python3 -m agents.bhaga.scripts.sandbox_e2e --store palmetto \
        --pr-number 42 --start 2026-05-01 --end 2026-05-03
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts import backfill_from_downloads, gcs_cache, sandbox_provision, update_model_sheet  # noqa: E402
from agents.bhaga.scripts.daily_refresh import MODEL_VERIFY_MIN_ROWS, assert_model_tabs_populated  # noqa: E402
from core.config_loader import refresh_access_token  # noqa: E402

DOWNLOADS = backfill_from_downloads.DOWNLOADS

# Modules that, if ever imported by this runner's graph, would mean a scrape /
# login path is reachable. The no-OTP test asserts none of these load.
FORBIDDEN_MODULES: tuple[str, ...] = (
    "skills.square_tips.runner",
    "skills.adp_run_automation.runner",
    "skills.clickup_chat",
    "skills.slack.listener",
    "patchright",
    "playwright",
)


# ── Pure helpers (no I/O — unit-tested) ───────────────────────────


def dates_in_window(start: datetime.date, end: datetime.date) -> list[datetime.date]:
    """Inclusive list of dates from start..end. Raises if end < start."""
    if end < start:
        raise ValueError(f"end {end} is before start {start}")
    return [start + datetime.timedelta(days=i) for i in range((end - start).days + 1)]


def select_window(cached_dates: list[datetime.date], max_days: int) -> tuple[datetime.date, datetime.date]:
    """Pick the most recent ``max_days`` *cached* dates and return (start, end).

    NOTE: the returned (start, end) spans the ``max_days`` most-recent dates that
    actually have artifacts in GCS — NOT ``max_days`` calendar days. With a sparse
    cache the calendar span (end - start) can exceed ``max_days``; that's fine,
    because the replay only touches dates that are cache-backed. Keeps the e2e
    window small (cost) and always cache-backed (determinism), independent of any
    hardcoded calendar range. Raises if no dates are cached.
    """
    if not cached_dates:
        raise ValueError("no cached dates in GCS — cannot auto-select an e2e window")
    if max_days < 1:
        raise ValueError(f"max_days must be >= 1, got {max_days}")
    recent = sorted(cached_dates)[-max_days:]
    return recent[0], recent[-1]


def staging_environ(ids: dict[str, str]) -> dict[str, str]:
    """Env overlay that routes the pipeline to the sandbox sheets.

    BHAGA_SHEET_MODE=staging activates the resolve_sheet_id staging branch and
    the _assert_not_production_sheet guard; the per-key SIDs point it at the
    sandbox.
    """
    env = dict(sandbox_provision.staging_env(ids))
    env["BHAGA_SHEET_MODE"] = "staging"
    return env


def tab_counts_from_columns(raw: dict[str, list[list]]) -> dict[str, int]:
    """{tab: value_rows_incl_header} -> {tab: data_row_count} (header excluded)."""
    return {tab: max(0, len(rows) - 1) for tab, rows in raw.items()}


def format_evidence(report: dict) -> str:
    """Render a human/PR-comment-friendly evidence block from a run report."""
    lines: list[str] = []
    lines.append(f"### BHAGA sandbox e2e — PR #{report.get('pr_number')}")
    lines.append(f"- status: **{report.get('status', 'unknown')}**")
    win = report.get("window", {})
    lines.append(f"- window: {win.get('start')} -> {win.get('end')} ({report.get('days')} day(s))")
    restored = report.get("restored_files", {})
    if restored:
        lines.append(f"- GCS files restored: {sum(restored.values())} "
                     f"({', '.join(f'{d}:{n}' for d, n in sorted(restored.items()))})")
    counts = report.get("model_tab_counts") or {}
    if counts:
        lines.append("- model tab row counts:")
        for tab in sorted(counts):
            lines.append(f"    - `{tab}`: {counts[tab]}")
    if report.get("item_lines_ran"):
        lines.append("- item-level operations backfill: ran (module present)")
    if report.get("error"):
        lines.append(f"- error: `{report['error']}`")
    teardown = report.get("teardown")
    if teardown is not None:
        lines.append(f"- teardown: deleted {len(teardown.get('deleted', []))} sheet(s)")
    return "\n".join(lines)


def _item_lines_module_available() -> bool:
    """True iff the (forward-compatible) item-lines backfill script exists."""
    return importlib.util.find_spec(
        "agents.bhaga.scripts.backfill_item_lines_from_cache"
    ) is not None


# ── Thin composition wrappers (I/O) ───────────────────────────────


def _apply_staging_env(ids: dict[str, str]) -> None:
    os.environ.update(staging_environ(ids))


def _invoke_main(main_fn, argv: list[str]) -> int:
    """Call a script's argparse main() with a controlled argv."""
    old = sys.argv
    sys.argv = ["sandbox_e2e"] + argv
    try:
        return main_fn() or 0
    finally:
        sys.argv = old


def _replay_from_gcs(start: datetime.date, end: datetime.date) -> dict[str, int]:
    """Restore cached scrape artifacts for the window into the downloads dir."""
    restored: dict[str, int] = {}
    for d in dates_in_window(start, end):
        files = gcs_cache.download_cached_files(refresh_date=d, download_dir=DOWNLOADS)
        restored[d.isoformat()] = len(files)
    return restored


def _run_backfill(store: str, start: datetime.date, end: datetime.date) -> int:
    return _invoke_main(
        backfill_from_downloads.main,
        ["--store", store, "--start", start.isoformat(), "--end", end.isoformat()],
    )


def _maybe_run_item_lines(store: str) -> bool:
    """Run the item-lines backfill if present (forward compat). Returns ran?"""
    if not _item_lines_module_available():
        return False
    import agents.bhaga.scripts.backfill_item_lines_from_cache as item_lines  # type: ignore
    _invoke_main(item_lines.main, ["--store", store, "--gcs-only"])
    return True


def _run_model_build(store: str) -> int:
    return _invoke_main(update_model_sheet.main, ["--store", store])


def _read_model_tab_counts(token: str, model_sid: str) -> dict[str, int]:
    raw: dict[str, list[list]] = {}
    for tab in MODEL_VERIFY_MIN_ROWS:
        rows = sandbox_provision._read_values(token, model_sid, f"{tab}!A1:A100000")
        raw[tab] = rows
    return tab_counts_from_columns(raw)


def run_e2e(
    *,
    store: str,
    pr_number: int,
    start: datetime.date,
    end: datetime.date,
    teardown_after: bool = True,
    expect_kds: bool = False,
) -> dict:
    """Full provision -> replay -> backfill -> model -> verify -> teardown loop.

    Teardown always runs (finally) unless ``teardown_after`` is False, so a
    failed run never leaks sandbox sheets.
    """
    report: dict = {
        "store": store,
        "pr_number": pr_number,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "days": len(dates_in_window(start, end)),
        "status": "error",
    }
    try:
        prov = sandbox_provision.provision(store=store, pr_number=pr_number)
        ids = prov["ids"]
        report["sandbox_ids"] = ids
        report["seed_counts"] = prov.get("seed_counts")
        _apply_staging_env(ids)

        account = sandbox_provision._load_pointer(store).get("google_account_key", store)
        token = refresh_access_token(account=account)

        report["restored_files"] = _replay_from_gcs(start, end)
        _run_backfill(store, start, end)
        report["item_lines_ran"] = _maybe_run_item_lines(store)
        _run_model_build(store)

        counts = _read_model_tab_counts(token, ids["bhaga_model"])
        report["model_tab_counts"] = counts
        assert_model_tabs_populated(tab_row_counts=counts, expect_kds=expect_kds)
        report["status"] = "ok"
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if teardown_after:
            report["teardown"] = sandbox_provision.teardown(store=store, pr_number=pr_number)
        print(format_evidence(report))
    return report


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--store", default="palmetto")
    cli.add_argument("--pr-number", type=int, required=True)
    cli.add_argument("--start", default=None, help="YYYY-MM-DD (inclusive). Omit with --auto-window.")
    cli.add_argument("--end", default=None, help="YYYY-MM-DD (inclusive). Omit with --auto-window.")
    cli.add_argument("--auto-window", action="store_true",
                     help="Auto-select up to the last --max-days *cached* dates from GCS "
                          "(deterministic + always cache-backed; preferred in CI).")
    cli.add_argument("--max-days", type=int, default=2,
                     help="Max number of most-recent cached days to replay for --auto-window "
                          "(the calendar span may be wider if the cache is sparse). Kept small for cost.")
    cli.add_argument("--keep", action="store_true",
                     help="Do NOT tear down the sandbox sheets after the run (debugging).")
    cli.add_argument("--expect-kds", action="store_true",
                     help="Also assert KDS-specific model invariants (cache must contain KDS).")
    cli.add_argument("--evidence-file", default=None,
                     help="Write the evidence block to this path (e.g. for a PR comment).")
    args = cli.parse_args(argv)

    if args.auto_window:
        start, end = select_window(gcs_cache.list_cached_dates(), args.max_days)
        print(f"# auto-selected window from GCS cache: {start} -> {end}")
    elif args.start and args.end:
        start = datetime.date.fromisoformat(args.start)
        end = datetime.date.fromisoformat(args.end)
    else:
        cli.error("provide --start and --end, or --auto-window")
    report = run_e2e(
        store=args.store,
        pr_number=args.pr_number,
        start=start,
        end=end,
        teardown_after=not args.keep,
        expect_kds=args.expect_kds,
    )
    if args.evidence_file:
        with open(args.evidence_file, "w") as f:
            f.write(format_evidence(report) + "\n")
    print("# report:")
    print(json.dumps(report, indent=2, default=str))
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
