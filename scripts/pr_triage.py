#!/usr/bin/env python3
"""One-shot PR triage aggregator — collect ALL merge-blocking signals in a single read-only pass.

Problem this solves
-------------------
The old babysit loop was serial: find one issue → fix → push → wait for Opus review → repeat.
Every completed push triggers a paid Claude Opus review (~$2-4 each). Batching all fixes into
one push reduces N paid reviews down to 1.

This script is the "collect-all" half of the batch loop. Run it FIRST to enumerate every
blocking signal, fix everything, then push ONCE.

Sections collected
------------------
- unresolved_threads: inline review-comment threads with no reply (classified by author type)
- failing_checks:     CI checks in FAILURE / ERROR / CANCELLED state
- merge_status:       BEHIND base or DIRTY (merge conflict) flags
- claude_verdict:     latest Claude bot verdict + evidence-confidence score + gap suggestions

Usage
-----
    python3 scripts/pr_triage.py [--pr N] [--repo owner/name] [--json]

Exit codes
----------
  0 = all clear (merge-ready)
  1 = work remaining (at least one blocking signal found)
  2 = tooling error (gh CLI missing, PR not found, etc.)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any


# ---------------------------------------------------------------------------
# gh helpers (mirrors check_pr_review_replies.py)
# ---------------------------------------------------------------------------

def _gh_json(args: list[str]) -> Any:
    try:
        out = subprocess.run(
            ["gh", *args], capture_output=True, text=True, check=True
        ).stdout
    except FileNotFoundError:
        print("error: `gh` CLI not found on PATH.", file=sys.stderr)
        sys.exit(2)
    except subprocess.CalledProcessError as e:
        print(f"error: gh {' '.join(args)} failed:\n{e.stderr}", file=sys.stderr)
        sys.exit(2)
    return json.loads(out) if out.strip() else []


def _current_pr() -> int | None:
    data = _gh_json(["pr", "view", "--json", "number"])
    return data.get("number") if isinstance(data, dict) else None


def _repo() -> str:
    data = _gh_json(["repo", "view", "--json", "nameWithOwner"])
    return data["nameWithOwner"]


# ---------------------------------------------------------------------------
# Author-class classifier
# ---------------------------------------------------------------------------

def _author_class(login: str) -> str:
    """Return 'claude-bot', 'bugbot', or 'human' for a comment author login."""
    lo = (login or "").lower()
    if lo.startswith("claude"):
        return "claude-bot"
    if "bugbot" in lo or lo.startswith("cursor"):
        return "bugbot"
    return "human"


# ---------------------------------------------------------------------------
# Section collectors
# ---------------------------------------------------------------------------

def _collect_unresolved_threads(pr: int, repo: str) -> list[dict]:
    """Return inline review-comment roots that have no reply and are not resolved."""
    comments = _gh_json(["api", "--paginate", f"repos/{repo}/pulls/{pr}/comments"])

    roots: dict[int, dict] = {}
    replied_roots: set[int] = set()

    for c in comments:
        cid = c["id"]
        if not c.get("in_reply_to_id"):
            # GitHub marks a thread resolved via pull_request_review_comment.pull_request_review_url
            # We treat it as resolved if subject_type hint present OR outdated flag is set and
            # there's no body — best available without extra API call. Simpler: include all roots,
            # filter replied ones.
            roots[cid] = c
        else:
            replied_roots.add(c["in_reply_to_id"])

    unaddressed = [c for cid, c in roots.items() if cid not in replied_roots]

    result = []
    for c in unaddressed:
        result.append({
            "id": c["id"],
            "author": c.get("user", {}).get("login", ""),
            "author_class": _author_class(c.get("user", {}).get("login", "")),
            "path": c.get("path", ""),
            "line": c.get("line") or c.get("original_line"),
            "body_snippet": " ".join((c.get("body") or "").split())[:120],
        })
    return result


_FAILING_STATES = {"FAILURE", "ERROR", "CANCELLED"}
_PENDING_STATES = {"PENDING", "IN_PROGRESS", "QUEUED", "WAITING"}
_LOG_TAIL_LINES = 50
# Matches GitHub Actions URLs: .../runs/<run_id>/job/<job_id>
_RUN_JOB_RE = re.compile(r"/runs/(\d+)/job/(\d+)")

# Waiver / confidence-floor constants (mirror check_evidence_confidence.py)
_CONFIDENCE_FLOOR = 95
_WAIVER_FLOOR = 80
_WAIVER_PATTERN = re.compile(
    r"Evidence\s+tier:\s*unit-only\b[^\n]*waiver\s*:\s*\S",
    re.IGNORECASE,
)


def _parse_run_job(link: str) -> tuple[str | None, str | None]:
    """Extract run_id and job_id from a GitHub Actions URL, or return (None, None)."""
    m = _RUN_JOB_RE.search(link or "")
    return (m.group(1), m.group(2)) if m else (None, None)


def _fetch_log_tail(run_id: str, job_id: str, max_lines: int = _LOG_TAIL_LINES) -> str:
    """Return the last `max_lines` lines of the failing job's log, or '' on any error.

    Never raises or calls sys.exit — callers treat empty string as "log unavailable".
    """
    try:
        out = subprocess.run(
            ["gh", "run", "view", run_id, "--job", job_id, "--log-failed"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""
    lines = out.splitlines()
    return "\n".join(lines[-max_lines:])


def _collect_failing_checks(pr: int) -> list[dict]:
    """Return CI checks that are failing/erroring/cancelled, with inline log tail."""
    raw = _gh_json(["pr", "checks", str(pr), "--json", "name,state,link"])
    if not isinstance(raw, list):
        return []
    result = []
    for c in raw:
        if (c.get("state") or "").upper() not in _FAILING_STATES:
            continue
        link = c.get("link", "")
        run_id, job_id = _parse_run_job(link)
        log_tail = _fetch_log_tail(run_id, job_id) if run_id and job_id else ""
        result.append({
            "name": c.get("name", ""),
            "state": c.get("state", ""),
            "link": link,
            "log_tail": log_tail,
        })
    return result


def _collect_pending_checks(pr: int) -> list[dict]:
    """Return CI checks that are still running (PENDING / IN_PROGRESS / QUEUED / WAITING).

    Race-safety guarantee: if a pending check later fails, the merge-protection gate
    blocks the merge and the *next* pr_triage round picks it up in failing_checks.
    Nothing is silently lost — pending is informational about "not ready yet", not "clean".
    """
    raw = _gh_json(["pr", "checks", str(pr), "--json", "name,state,link"])
    if not isinstance(raw, list):
        return []
    return [
        {"name": c.get("name", ""), "state": c.get("state", ""), "link": c.get("link", "")}
        for c in raw
        if (c.get("state") or "").upper() in _PENDING_STATES
    ]


def _pr_has_waiver(pr: int, repo: str) -> bool:
    """Return True if this PR carries a unit-only evidence waiver.

    Checks two sources (either sufficient):
    1. PR body containing 'Evidence tier: unit-only (waiver: ...)'.
    2. 'evidence-waiver' label on the PR.

    Returns False on any API error (safe default: apply stricter floor).
    Mirrors the convention in check_evidence_confidence.py::_has_waiver().
    """
    try:
        data = _gh_json(["pr", "view", str(pr), "--repo", repo, "--json", "body,labels"])
    except SystemExit:
        return False
    if not isinstance(data, dict):
        return False
    labels = {(lbl.get("name") or "") for lbl in (data.get("labels") or [])}
    if "evidence-waiver" in labels:
        return True
    return bool(_WAIVER_PATTERN.search(data.get("body") or ""))


def _collect_merge_status(pr: int, repo: str) -> dict:
    """Return behind/conflict flags."""
    data = _gh_json(["pr", "view", str(pr), "--json", "mergeable,mergeStateStatus"])
    if not isinstance(data, dict):
        return {"behind": False, "conflict": False, "raw": ""}
    state = (data.get("mergeStateStatus") or "").upper()
    mergeable = (data.get("mergeable") or "").upper()
    return {
        "behind": state == "BEHIND",
        "conflict": state == "DIRTY" or mergeable == "CONFLICTING",
        "raw": state,
    }


# Patterns for parsing the Claude summary comment
_CONFIDENCE_RE = re.compile(
    r"Evidence confidence(?:\s+rating)?\s*[:*\s]+\*{0,2}(\d+)\s*%",
    re.IGNORECASE,
)
_GAP_SECTION_RE = re.compile(
    r"Evidence gaps.*?(?=\n##|\Z)",
    re.IGNORECASE | re.DOTALL,
)


def _collect_claude_verdict(pr: int, repo: str) -> dict:
    """Return latest Claude bot verdict, evidence-confidence, and gap suggestions."""
    comments = _gh_json(
        ["api", "--paginate", f"repos/{repo}/issues/{pr}/comments"]
    )
    if not isinstance(comments, list):
        return {}

    claude_comments = [
        c for c in comments
        if (c.get("user", {}).get("login") or "").lower().startswith("claude")
    ]
    if not claude_comments:
        return {}

    latest = claude_comments[-1]
    body = latest.get("body") or ""
    first_lines = "\n".join(body.splitlines()[:3]).upper()

    if "REQUEST CHANGES" in first_lines:
        verdict = "REQUEST_CHANGES"
    elif "APPROVE" in first_lines:
        verdict = "APPROVE"
    else:
        verdict = "UNKNOWN"

    confidence: int | None = None
    m = _CONFIDENCE_RE.search(body)
    if m:
        confidence = int(m.group(1))

    gap_text = ""
    gm = _GAP_SECTION_RE.search(body)
    if gm:
        gap_text = gm.group(0).strip()[:400]

    return {
        "verdict": verdict,
        "confidence": confidence,
        "evidence_gaps": gap_text,
        "comment_url": latest.get("html_url", ""),
    }


# ---------------------------------------------------------------------------
# Top-level collect
# ---------------------------------------------------------------------------

def collect(pr: int, repo: str) -> dict:
    """Run all sections and return the consolidated triage dict."""
    return {
        "pr": pr,
        "repo": repo,
        "unresolved_threads": _collect_unresolved_threads(pr, repo),
        "failing_checks": _collect_failing_checks(pr),
        "pending_checks": _collect_pending_checks(pr),
        "merge_status": _collect_merge_status(pr, repo),
        "claude_verdict": _collect_claude_verdict(pr, repo),
        "has_waiver": _pr_has_waiver(pr, repo),
    }


def _has_work(triage: dict) -> bool:
    """Return True if any blocking signal exists.

    Pending checks are blocking — they mean "wait, don't push yet". If they
    later fail the merge-protection gate catches them; next triage round surfaces them.
    The confidence floor is lowered to _WAIVER_FLOOR (80) when the PR carries a
    unit-only evidence waiver (mirrors check_evidence_confidence.py behaviour).
    """
    if triage["unresolved_threads"]:
        return True
    if triage["failing_checks"]:
        return True
    if triage.get("pending_checks"):
        return True
    ms = triage["merge_status"]
    if ms.get("behind") or ms.get("conflict"):
        return True
    cv = triage["claude_verdict"]
    if cv.get("verdict") == "REQUEST_CHANGES":
        return True
    floor = _WAIVER_FLOOR if triage.get("has_waiver") else _CONFIDENCE_FLOOR
    if cv.get("confidence") is not None and cv["confidence"] < floor:
        return True
    return False


# ---------------------------------------------------------------------------
# Human-readable output
# ---------------------------------------------------------------------------

def _print_report(triage: dict) -> None:
    pr = triage["pr"]
    print(f"\n=== PR #{pr} triage report ===\n")

    # Merge status
    ms = triage["merge_status"]
    if ms.get("behind"):
        print("MERGE STATUS: BEHIND base — merge or rebase before pushing.")
    if ms.get("conflict"):
        print("MERGE STATUS: CONFLICT (DIRTY) — resolve merge conflicts.")
    if not ms.get("behind") and not ms.get("conflict"):
        print("merge status: clean")

    # Pending CI
    pending = triage.get("pending_checks", [])
    if pending:
        print(f"\nCI: {len(pending)} check(s) still running — wait before pushing:")
        for c in pending:
            print(f"  [{c['state']}] {c['name']}")
            if c.get("link"):
                print(f"         {c['link']}")

    # Failing CI
    checks = triage["failing_checks"]
    if checks:
        print(f"\nFAILING CHECKS ({len(checks)}):")
        for c in checks:
            print(f"  [{c['state']}] {c['name']}")
            if c.get("link"):
                print(f"         {c['link']}")
            if c.get("log_tail"):
                print("         --- log tail ---")
                for line in c["log_tail"].splitlines():
                    print(f"         {line}")
                print("         --- end log tail ---")
    elif not pending:
        print("\nCI checks: all passing")

    # Claude verdict
    cv = triage["claude_verdict"]
    if cv:
        verdict_str = cv.get("verdict", "UNKNOWN")
        conf = cv.get("confidence")
        has_waiver = triage.get("has_waiver", False)
        floor = _WAIVER_FLOOR if has_waiver else _CONFIDENCE_FLOOR
        conf_str = f"  Evidence confidence: {conf}% (floor: {floor}%{', waiver active' if has_waiver else ''})" if conf is not None else ""
        print(f"\nCLAUDE VERDICT: {verdict_str}{conf_str}")
        if cv.get("evidence_gaps"):
            print(f"  Evidence gaps:\n    {cv['evidence_gaps'][:300]}")
        if cv.get("comment_url"):
            print(f"  Comment: {cv['comment_url']}")
    else:
        print("\nClaude verdict: no review posted yet")

    # Unresolved threads
    threads = triage["unresolved_threads"]
    if threads:
        print(f"\nUNRESOLVED THREADS ({len(threads)}) — reply on each before pushing:")
        by_class: dict[str, list] = {}
        for t in threads:
            by_class.setdefault(t["author_class"], []).append(t)
        for cls in ("claude-bot", "bugbot", "human"):
            if cls not in by_class:
                continue
            print(f"  [{cls}]")
            for t in by_class[cls]:
                loc = f"{t['path']}:{t['line']}" if t.get("path") else "(no location)"
                print(f"    id={t['id']} {loc}")
                print(f"      {t['body_snippet']}")
                print(
                    f"    → gh api repos/{triage['repo']}/pulls/{pr}/comments/"
                    f"{t['id']}/replies -f body='fixed in <sha> / won't fix: <reason>'"
                )
    else:
        print("\ninline threads: all replied")

    # Summary
    print()
    if _has_work(triage):
        print("RESULT: work remaining — fix ALL items above, then push ONCE.")
    else:
        print("RESULT: all clear — PR is merge-ready.")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pr", type=int, default=None, help="PR number (default: current branch's PR)")
    ap.add_argument("--repo", default=None, help="owner/name (default: current repo)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of human output")
    args = ap.parse_args(argv)

    repo = args.repo or _repo()
    pr = args.pr or _current_pr()
    if not pr:
        print("error: no PR found for the current branch (pass --pr N).", file=sys.stderr)
        return 2

    triage = collect(pr, repo)

    if args.json:
        print(json.dumps(triage, indent=2))
    else:
        _print_report(triage)

    return 1 if _has_work(triage) else 0


if __name__ == "__main__":
    sys.exit(main())
