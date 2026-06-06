#!/usr/bin/env python3
"""BHAGA sandbox scenario suite — a named, selectable set of LIVE sandbox runs.

WHY
  The replay e2e proves the pure core; some failures (selector drift, login/2FA,
  browser crashes) are *live-only*. This registry turns those into named scenarios
  you run **selectively** and **on demand**, each posting evidence — so a PR can
  prove a live fix without merging blind or running ad-hoc scripts.

HOW YOU SELECT WHAT RUNS (two ways, both route here)
  1. Committed config — `.github/sandbox-live.yml` lists scenarios to run; the
     `pull_request` workflow runs them and posts evidence. Works PRE-MERGE (the
     workflow file is read from the PR merge ref). Empty/absent file = nothing runs
     (i.e. "turn them off" once you've captured evidence).
  2. PR comment — `/sandbox run <scenario> [date=YYYY-MM-DD]` triggers a one-shot
     run and posts the result. Uses `issue_comment`, so it only works once this
     workflow is on the DEFAULT branch (steady-state control for future PRs).

Each scenario ultimately drives `sandbox_live_run` (full isolation: reads prod,
writes only sandbox; OTP labeled [SANDBOX] and routed to the sandbox job).
"""

from __future__ import annotations

import argparse
import re

# name → metadata. Add an entry to grow the suite; keep names kebab-case so they
# read cleanly as a PR label or a `/sandbox run <name>` command.
SCENARIOS: dict[str, dict] = {
    "item-sales-live": {
        "description": (
            "Live Square scrape for a date. Reproduces the item-sales date-picker "
            "selector drift; on failure the DOM + screenshot land in GCS evidence."
        ),
        # Scope to ONLY the Square data download (the surface that failed): skip
        # ADP, the Google-reviews refresh, and the final model rollup. Keeps the
        # full Square scrape (transactions + item-sales + KDS).
        "skip": ["adp", "reviews", "model"],
        # Post-run gate: the run is only a PASS if item-sales data was downloaded.
        "verify": "item_sales",
    },
    "full-live": {
        "description": "Full live pipeline (Square + ADP) for a date, against the sandbox.",
    },
    "unified-window": {
        "description": (
            "Full live pipeline over a closed historical window (Square + ADP timecard "
            "+ ADP earnings custom-range + reviews), proving all four sources honor "
            "--from/--to. window_from and window_to must be set in the scenario config."
        ),
        # No steps skipped — exercises the complete unified window fan-out.
        # Evidence: per-source row counts written to the evidence file.
    },
    "full-history-bq-sandbox": {
        "description": (
            "Scrape-from-source backfill of the ENTIRE history into the isolated "
            "bhaga_sandbox BQ dataset (Square + ADP timecard + ADP earnings + reviews). "
            "fresh_scrape forces the cache READ bucket to the empty sandbox bucket so "
            "every source is pulled from the actual upstream portals — never prod GCS "
            "or Sheets. window_from/window_to must be set (e.g. 2026-03-23 → last closed "
            "day). Evidence: per-source BQ row counts vs prod Sheets via verify_prod_parity."
        ),
        # No steps skipped — full fan-out to all sources.
        "fresh_scrape": True,
    },
}

_COMMENT_RE = re.compile(
    r"^/sandbox\s+run\s+(?P<name>[\w-]+)(?:\s+date=(?P<date>\d{4}-\d{2}-\d{2}))?\s*$",
    re.IGNORECASE,
)


def parse_comment(comment: str) -> dict | None:
    """Parse a `/sandbox run <scenario> [date=YYYY-MM-DD]` PR comment.

    Returns {"name", "date"} (date may be None) or None if the comment isn't a
    sandbox command or names an unknown scenario.
    """
    if not comment:
        return None
    m = _COMMENT_RE.match(comment.strip())
    if not m:
        return None
    name = m.group("name").lower()
    if name not in SCENARIOS:
        return None
    return {"name": name, "date": m.group("date")}


def load_config(path: str) -> list[dict]:
    """Read the committed scenario list from `.github/sandbox-live.yml`.

    Shape: ``{scenarios: [{name: <scenario>, date: YYYY-MM-DD}, ...]}``. Returns
    [] when the file is absent or empty (scenarios turned off). Unknown scenario
    names raise so a typo fails loud rather than silently skipping.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return []
    import yaml  # lazy: only needed when a config exists

    data = yaml.safe_load(text) or {}
    out: list[dict] = []
    for item in data.get("scenarios", []) or []:
        name = str(item.get("name", "")).lower()
        if name not in SCENARIOS:
            raise ValueError(f"unknown sandbox scenario {name!r}; known: {sorted(SCENARIOS)}")
        # PyYAML coerces an unquoted YYYY-MM-DD to a date; normalize to an ISO
        # string since downstream passes it as a CLI arg.
        date = item.get("date")
        if hasattr(date, "isoformat"):
            date = date.isoformat()
        window_from = item.get("window_from")
        if hasattr(window_from, "isoformat"):
            window_from = window_from.isoformat()
        window_to = item.get("window_to")
        if hasattr(window_to, "isoformat"):
            window_to = window_to.isoformat()
        out.append({"name": name, "date": date, "window_from": window_from, "window_to": window_to})
    return out


def run_scenario(
    name: str,
    *,
    date: str,
    pr_number: int,
    pr_label: str,
    image: str,
    evidence_file: str | None = None,
    no_execute: bool = False,
    window_from: str | None = None,
    window_to: str | None = None,
) -> int:
    """Drive one scenario through sandbox_live_run. Returns its exit code."""
    if name not in SCENARIOS:
        raise ValueError(f"unknown scenario {name!r}")
    from agents.bhaga.scripts import sandbox_live_run  # lazy: heavy GCP deps
    meta = SCENARIOS[name]
    label = f"{name} · PR#{pr_number} {pr_label}".strip()
    argv = [
        "--store", "palmetto",
        "--pr-number", str(pr_number),
        "--pr-label", label,
        "--refresh-date", date,
        "--image", image,
    ]
    skip = meta.get("skip") or []
    if skip:
        argv += ["--skip", ",".join(skip)]
    if meta.get("verify"):
        argv += ["--verify", meta["verify"]]
    if meta.get("fresh_scrape"):
        argv.append("--fresh-scrape")
    if evidence_file:
        argv += ["--evidence-file", evidence_file]
    if no_execute:
        argv.append("--no-execute")
    # Unified window: thread --from/--to from scenario config or caller override.
    _wf = window_from or meta.get("window_from")
    _wt = window_to or meta.get("window_to")
    if _wf:
        argv += ["--from", _wf]
    if _wt:
        argv += ["--to", _wt]
    print(f"[scenario:{name}] {meta['description']}")
    return sandbox_live_run.main(argv)


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = cli.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List available scenarios.")

    run = sub.add_parser("run", help="Run one scenario.")
    run.add_argument("name", choices=sorted(SCENARIOS))
    run.add_argument("--date", required=True)
    run.add_argument("--pr-number", type=int, required=True)
    run.add_argument("--pr-label", default="")
    run.add_argument("--image", required=True)
    run.add_argument("--evidence-file")
    run.add_argument("--no-execute", action="store_true")
    run.add_argument("--from", dest="window_from", default=None, metavar="DATE",
                     help="Unified window START (YYYY-MM-DD). Overrides scenario meta.")
    run.add_argument("--to", dest="window_to", default=None, metavar="DATE",
                     help="Unified window END (YYYY-MM-DD). Overrides scenario meta.")

    args = cli.parse_args(argv)

    if args.cmd == "list":
        for name, meta in sorted(SCENARIOS.items()):
            print(f"  {name:18s} {meta['description']}")
        return 0

    return run_scenario(
        args.name, date=args.date, pr_number=args.pr_number, pr_label=args.pr_label,
        image=args.image, evidence_file=args.evidence_file, no_execute=args.no_execute,
        window_from=getattr(args, "window_from", None),
        window_to=getattr(args, "window_to", None),
    )


if __name__ == "__main__":
    raise SystemExit(main())
