#!/usr/bin/env python3
"""One front door for "start a new task" on any surface (the `/jarvis-new-task` skill runs this).

Where the task runs is decided for you, and can be overridden by the first word of the text:

    in a Mac chat          → Mac worktree (new_requirement.py), unless the text starts with `cloud`
    in a Cloud Agent chat  → a new Cloud Agent (issue + `/jarvis-new-task` comment), unless it starts with `mac`
    a GitHub comment       → cloud by default, `mac` first word → Mac (jarvis-dev-signals.yml routes it)

    python3 scripts/jarvis_task.py "[mac|cloud] <requirement>"   [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

WHERE = ("mac", "cloud")


def parse(text: str) -> tuple[str | None, str]:
    """Split an optional leading `mac`/`cloud` (or `where=mac`) off the requirement."""
    m = re.match(r"^\s*(?:where=)?(mac|cloud)\b[:,]?\s*(.*)$", text or "", re.I | re.S)
    return (m.group(1).lower(), m.group(2).strip()) if m else (None, (text or "").strip())


def default_where(surface: str) -> str:
    return "cloud" if surface == "cursor_cloud" else "mac"


def issue_ref(req: str) -> str | None:
    m = re.search(r"(?:#|/issues/)(\d+)", req)
    return m.group(1) if m else None


def cloud_commands(req: str) -> list[list[str]]:
    """gh calls that hand the task to the `/jarvis-new-task` comment workflow (cloud intake)."""
    comment = f"/jarvis-new-task {req}"
    num = issue_ref(req)
    if num:
        return [["gh", "issue", "comment", num, "--body", comment]]
    title = (req.splitlines() or [req])[0][:80]
    return [["gh", "issue", "create", "--title", title, "--body", req, "--label", "jarvis-work"]]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--surface", help="override detection (mac_local|cursor_cloud)")
    a = ap.parse_args(argv)

    from gcp_access_probe import detect_surface
    where, req = parse(a.text)
    if not req:
        sys.exit("jarvis_task: no requirement text")
    where = where or default_where(a.surface or detect_surface())
    print(f"jarvis_task: starting on {where}")

    if where == "mac":
        cmd = [sys.executable, str(ROOT / "scripts" / "new_requirement.py"), "--requirement", req]
        if a.dry_run:
            print("would run:", cmd)
            return 0
        return subprocess.call(cmd)

    cmds = cloud_commands(req)
    for c in cmds:
        print("would run:" if a.dry_run else "run:", c[:4], "…")
    if a.dry_run:
        return 0
    out = ""
    for c in cmds:
        r = subprocess.run(c, capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"jarvis_task: {' '.join(c[:3])} failed: {r.stderr.strip()}")
        out = r.stdout.strip()
    num = issue_ref(out) or issue_ref(req)
    if "issue create" in " ".join(cmds[0][:3]) and num:
        r = subprocess.run(["gh", "issue", "comment", num, "--body", f"/jarvis-new-task {req}"],
                           capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"jarvis_task: comment failed: {r.stderr.strip()}")
    print(f"jarvis_task: issue #{num} — the cloud agent link is commented on it in about a minute")
    return 0


if __name__ == "__main__":
    sys.exit(main())
