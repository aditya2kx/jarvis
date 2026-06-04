#!/usr/bin/env python3
"""CLI helper to update requirement status in Playground/REQUIREMENTS.md.

Used by:
  - start_pr_session.py  (→ 🔄 In Progress when a session starts)
  - pr-cost-finalize.yml (→ ✅ Done when a PR merges)
  - agents / humans directly for ad-hoc updates

Usage:
    python3 scripts/requirements_tracker.py mark-done   --req 15 --pr 22
    python3 scripts/requirements_tracker.py mark-progress --req 15 --pr 22
    python3 scripts/requirements_tracker.py add          --req "Short title" [--priority p0]
    python3 scripts/requirements_tracker.py list
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_REQUIREMENTS_MD = Path(__file__).parent.parent / "Playground" / "REQUIREMENTS.md"

_STATUS_PENDING     = "🔲 Pending"
_STATUS_IN_PROGRESS = "🔄 In Progress"
_STATUS_DONE        = "✅ Done"
_STATUS_P0          = "🔴 P0"

_ALL_STATUSES = [_STATUS_PENDING, _STATUS_IN_PROGRESS, _STATUS_DONE, _STATUS_P0]


def _row_pattern(req_id: str) -> re.Pattern:
    escaped = "|".join(re.escape(s) for s in _ALL_STATUSES)
    return re.compile(
        r"^(\| *(?:" + escaped + r") *\| *" + re.escape(str(req_id)) + r" *\|.*)",
        re.MULTILINE,
    )


def _update_status(req_id: str, new_status: str, pr: int | None = None) -> bool:
    if not _REQUIREMENTS_MD.exists():
        print(f"error: {_REQUIREMENTS_MD} not found", file=sys.stderr)
        return False
    text = _REQUIREMENTS_MD.read_text(encoding="utf-8")
    pattern = _row_pattern(req_id)
    match = pattern.search(text)
    if not match:
        return False
    old_row = match.group(1)
    escaped = "|".join(re.escape(s) for s in _ALL_STATUSES)
    new_row = re.sub(
        r"^(\| *)(?:" + escaped + r")( *\|)",
        rf"\g<1>{new_status}\g<2>",
        old_row,
    )
    if pr is not None:
        cols = new_row.split("|")
        if len(cols) > 4:
            pr_cell = cols[4].strip()
            pr_ref = f"#{pr}"
            if pr_ref not in pr_cell:
                cols[4] = f" {pr_cell + ', ' if pr_cell not in ('', '—') else ''}{pr_ref} "
                new_row = "|".join(cols)
    _REQUIREMENTS_MD.write_text(
        text[: match.start()] + new_row + text[match.end():],
        encoding="utf-8",
    )
    return True


def _next_id() -> int:
    if not _REQUIREMENTS_MD.exists():
        return 1
    text = _REQUIREMENTS_MD.read_text(encoding="utf-8")
    escaped = "|".join(re.escape(s) for s in _ALL_STATUSES)
    ids = re.findall(r"^\| *(?:" + escaped + r") *\| *(\d+) *\|", text, re.MULTILINE)
    return max((int(i) for i in ids), default=0) + 1


def cmd_mark_done(req_id: str, pr: int | None) -> int:
    ok = _update_status(req_id, _STATUS_DONE, pr=pr)
    if ok:
        print(f"✅ Requirement #{req_id} marked Done" + (f" (PR #{pr})" if pr else ""))
    else:
        print(f"⚠️  Requirement #{req_id} not found in {_REQUIREMENTS_MD}")
        return 1
    return 0


def cmd_mark_progress(req_id: str, pr: int | None) -> int:
    ok = _update_status(req_id, _STATUS_IN_PROGRESS, pr=pr)
    if ok:
        print(f"🔄 Requirement #{req_id} marked In Progress" + (f" (PR #{pr})" if pr else ""))
    else:
        print(f"⚠️  Requirement #{req_id} not found in {_REQUIREMENTS_MD}")
        return 1
    return 0


def cmd_add(title: str, priority: str | None) -> int:
    nid = _next_id()
    status = _STATUS_P0 if priority == "p0" else _STATUS_PENDING
    row = f"| {status} | {nid} | {title} | — | |\n"
    text = _REQUIREMENTS_MD.read_text(encoding="utf-8") if _REQUIREMENTS_MD.exists() else ""
    # Insert before the Archive section (or at end)
    archive_idx = text.find("\n---\n")
    if archive_idx != -1:
        text = text[: archive_idx] + "\n" + row + text[archive_idx:]
    else:
        text += row
    _REQUIREMENTS_MD.write_text(text, encoding="utf-8")
    print(f"Added requirement #{nid}: {title}")
    return 0


def cmd_list() -> int:
    if not _REQUIREMENTS_MD.exists():
        print("REQUIREMENTS.md not found")
        return 1
    text = _REQUIREMENTS_MD.read_text(encoding="utf-8")
    escaped = "|".join(re.escape(s) for s in _ALL_STATUSES)
    rows = re.findall(
        r"^\| *((?:" + escaped + r")) *\| *(\d+) *\| *([^|]+)\|",
        text, re.MULTILINE,
    )
    for status, rid, title in rows:
        print(f"  [{status.strip()}] #{rid}: {title.strip()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = cli.add_subparsers(dest="cmd", required=True)

    p_done = sub.add_parser("mark-done", help="Mark a requirement ✅ Done")
    p_done.add_argument("--req", required=True, help="Requirement ID")
    p_done.add_argument("--pr", type=int, help="PR number to record")

    p_prog = sub.add_parser("mark-progress", help="Mark a requirement 🔄 In Progress")
    p_prog.add_argument("--req", required=True, help="Requirement ID")
    p_prog.add_argument("--pr", type=int, help="PR number to record")

    p_add = sub.add_parser("add", help="Add a new requirement")
    p_add.add_argument("--req", required=True, dest="title", help="Short title")
    p_add.add_argument("--priority", choices=["p0"], help="Set P0 status")

    sub.add_parser("list", help="List all requirements with status")

    args = cli.parse_args(argv)
    if args.cmd == "mark-done":
        return cmd_mark_done(args.req, args.pr)
    if args.cmd == "mark-progress":
        return cmd_mark_progress(args.req, args.pr)
    if args.cmd == "add":
        return cmd_add(args.title, getattr(args, "priority", None))
    if args.cmd == "list":
        return cmd_list()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
