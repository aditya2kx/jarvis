#!/usr/bin/env python3
"""Resolve what the Sandbox-live-run workflow should run, from the triggering event.

Reads the event context from env vars (set in the workflow), decides whether to
run and which scenario(s)/date(s), and writes GITHUB_OUTPUT:
    run        — "true"/"false"
    pr_number  — PR number for the slot lease + evidence comment ("" if none)
    head_ref   — branch to check out + build (the PR head)
    plan       — JSON list of {"name","date"}

Kept dependency-light (stdlib + sandbox_scenarios' pure helpers) so the resolve
job needn't install the full orchestrator requirements.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys

sys.path.insert(0, os.getcwd())

from agents.bhaga.scripts import sandbox_scenarios  # noqa: E402

_AUTHORIZED = {"OWNER", "COLLABORATOR", "MEMBER"}


def _emit(run: bool, *, pr_number: str = "", head_ref: str = "", plan: list | None = None) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    lines = [
        f"run={'true' if run else 'false'}",
        f"pr_number={pr_number}",
        f"head_ref={head_ref}",
        f"plan={json.dumps(plan or [])}",
    ]
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))


def _yesterday_ct() -> str:
    try:
        import zoneinfo
        now = datetime.datetime.now(zoneinfo.ZoneInfo("America/Chicago"))
    except Exception:  # noqa: BLE001
        # zoneinfo is stdlib since 3.9 and the workflow is 3.12, so this should
        # never fire. If it does, anchor to UTC-6 (CST): CT is never *ahead* of
        # UTC-6, so "yesterday" can't be computed a day early and trip the
        # CT-always invariant (a late-evening CT run mis-dated to the next UTC day).
        now = datetime.datetime.utcnow() - datetime.timedelta(hours=6)
    return (now.date() - datetime.timedelta(days=1)).isoformat()


def _require_dates(plan: list) -> None:
    for item in plan:
        if not item.get("date"):
            raise SystemExit(f"scenario {item['name']!r} is missing a date in .github/sandbox-live.yml")


def main() -> int:
    event = os.environ.get("EVENT_NAME", "")

    if event == "workflow_dispatch":
        scenario = os.environ.get("IN_SCENARIO", "")
        if scenario not in sandbox_scenarios.SCENARIOS:
            print(f"unknown scenario {scenario!r}")
            _emit(False)
            return 0
        _emit(True, pr_number=os.environ.get("IN_PR", ""),
              head_ref=os.environ.get("DISPATCH_REF", ""),
              plan=[{"name": scenario, "date": os.environ.get("IN_DATE", "")}])
        return 0

    if event == "pull_request":
        if os.environ.get("PR_IS_FORK") == "true":
            print("fork PR — refusing live sandbox (would expose secrets)")
            _emit(False)
            return 0
        if os.environ.get("PR_HAS_LABEL") != "true":
            print("PR not labeled 'sandbox-live' — skipping")
            _emit(False)
            return 0
        plan = sandbox_scenarios.load_config(".github/sandbox-live.yml")
        if not plan:
            print("no scenarios in .github/sandbox-live.yml — nothing to run")
            _emit(False)
            return 0
        _require_dates(plan)
        _emit(True, pr_number=os.environ.get("PR_NUMBER", ""),
              head_ref=os.environ.get("PR_HEAD_REF", ""), plan=plan)
        return 0

    if event == "issue_comment":
        if os.environ.get("CMT_IS_PR") != "true":
            _emit(False)
            return 0
        if os.environ.get("CMT_ASSOC", "") not in _AUTHORIZED:
            print(f"comment author '{os.environ.get('CMT_ASSOC')}' not authorized")
            _emit(False)
            return 0
        parsed = sandbox_scenarios.parse_comment(os.environ.get("CMT_BODY", ""))
        if not parsed:
            _emit(False)
            return 0
        num = os.environ.get("ISSUE_NUMBER", "")
        try:
            info = json.loads(subprocess.run(
                ["gh", "pr", "view", num, "--json", "headRefName,isCrossRepository"],
                capture_output=True, text=True, check=True).stdout or "{}")
        except Exception as exc:  # noqa: BLE001
            print(f"could not resolve PR head ref: {exc}")
            _emit(False)
            return 0
        if info.get("isCrossRepository"):
            print("cross-repo PR — refusing live sandbox")
            _emit(False)
            return 0
        date = parsed["date"] or _yesterday_ct()
        _emit(True, pr_number=num, head_ref=info.get("headRefName", ""),
              plan=[{"name": parsed["name"], "date": date}])
        return 0

    _emit(False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
