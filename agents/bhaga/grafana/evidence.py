#!/usr/bin/env python3
"""evidence.py — one command that produces a paste-ready PR §4 evidence block.

This is the single entrypoint for "prove my Grafana change didn't break
anything and here are the screenshots" — it exists so nobody has to
rediscover which of the scripts in this directory to run, in what order, or
how auth works (see README.md § Auth model: everything here uses a Grafana
Bearer token; NONE of it needs gcloud/ADC/config.yaml).

Runs, in order:
  1. verify_panels.py   — every panel returns data, no datasource/SQL errors
  2. compare_panels.py  — prod-vs-branch row-for-row parity (Issue #126 bar)
  3. capture_screenshot.py — before/after PNGs for the panels you changed,
     uploaded to a GitHub release, returning stable https:// URLs

Usage:
    python3 agents/bhaga/grafana/evidence.py --changed-panels 79 81
    python3 agents/bhaga/grafana/evidence.py --changed-panels 79 81 --compare-mode live
    python3 agents/bhaga/grafana/evidence.py --changed-panels 79 81 --skip-screenshots
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

_GRAFANA_DIR = pathlib.Path(__file__).resolve().parent


def _run(cmd: list[str], label: str) -> tuple[int, str]:
    print(f"\n{'=' * 70}\n[evidence] {label}\n{'=' * 70}", file=sys.stderr)
    proc = subprocess.run(cmd, cwd=_GRAFANA_DIR, capture_output=True, text=True)
    sys.stderr.write(proc.stderr)
    sys.stdout.write(proc.stdout)
    return proc.returncode, proc.stdout


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--changed-panels", type=int, nargs="+", required=True,
                    help="panel IDs your PR changed, e.g. --changed-panels 79 81")
    ap.add_argument("--compare-mode", choices=["inline", "live"], default="inline")
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--skip-screenshots", action="store_true",
                    help="skip capture_screenshot.py (e.g. no GITHUB_TOKEN available)")
    args = ap.parse_args()

    sections: list[str] = []
    overall_rc = 0

    rc, verify_out = _run(
        ["python3", "verify_panels.py"], "Step 1/3 — verify_panels.py (all panels return data)"
    )
    overall_rc |= rc
    sections.append(f"### Panel verification (`verify_panels.py`)\n```\n{verify_out.strip()}\n```")

    rc, compare_out = _run(
        ["python3", "compare_panels.py", "--base", args.base, "--mode", args.compare_mode],
        "Step 2/3 — compare_panels.py (prod-vs-branch parity)",
    )
    overall_rc |= rc
    sections.append(
        f"### Prod-vs-branch data parity (`compare_panels.py --mode {args.compare_mode}`)\n"
        f"```\n{compare_out.strip()}\n```"
    )

    if not args.skip_screenshots:
        panel_args = []
        for p in args.changed_panels:
            panel_args += ["--panel", str(p)]
        rc, shot_out = _run(
            ["python3", "capture_screenshot.py", *panel_args],
            "Step 3/3 — capture_screenshot.py (before/after PNGs)",
        )
        overall_rc |= rc
        shot_lines = [ln for ln in shot_out.strip().splitlines() if ": http" in ln]
        images_md = "\n".join(f"![{ln.split(':', 1)[0]}]({ln.split(': ', 1)[1]})" for ln in shot_lines)
        sections.append(f"### Panel screenshots\n{images_md or '(no screenshots captured)'}")

    print("\n\n" + "=" * 70)
    print("PR §4 EVIDENCE BLOCK — paste below")
    print("=" * 70 + "\n")
    print("\n\n".join(sections))

    return overall_rc


if __name__ == "__main__":
    sys.exit(main())
