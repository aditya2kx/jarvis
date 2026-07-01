#!/usr/bin/env python3
"""Work-phase tracker backed by GitHub Issues.

Each work item (branch) gets one GitHub issue whose labels and body encode the
current stage, progress %, done/remaining substeps, and any failures — visible
to every machine and cloud agent, not just the laptop that started the work.

The local file metrics/pr_cost/session-<slug>-phase.json is a per-branch cache
(issue number, done list, failures); GitHub is the source of truth.

Label set (created once with `ensure-labels`):
  jarvis-work               — find all Jarvis work items
  stage:align|plan|build|ship|learn  — current stage (one per issue)
  blocked                   — open failure/blocker exists
  awaiting:operator         — paused at operator-reserved gate
  approved:specify|jam|define-evidence|merge  — operator approval for each gate

Jira/Linear seam: the `source` field defaults to "github". When the backend
switches, swap source → "linear"/"jira" and point `_gh_*` helpers at the new
API; all callers stay the same.

Usage:
    python3 scripts/phase_state.py ensure-labels
    python3 scripts/phase_state.py init --branch feat/foo [--requirement-id 5] [--dry-run]
    python3 scripts/phase_state.py advance --branch feat/foo --to implement
    python3 scripts/phase_state.py fail --branch feat/foo --reason "CI red: secret-scan"
    python3 scripts/phase_state.py clear-fail --branch feat/foo
    python3 scripts/phase_state.py status [--branch feat/foo] [--json]
    python3 scripts/phase_state.py report
    python3 scripts/phase_state.py gate [--branch feat/foo]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import lifecycle as lc

REPO_ROOT = Path(__file__).parent.parent
METRICS_DIR = REPO_ROOT / "metrics" / "pr_cost"

# ---------------------------------------------------------------------------
# Label definitions
# ---------------------------------------------------------------------------

ALL_LABELS: list[dict] = [
    {"name": "jarvis-work",          "color": "0075ca", "description": "Jarvis work-tracking issue"},
    {"name": "stage:align",          "color": "e4e669", "description": "Stage: Align (specify/setup/jam/evidence)"},
    {"name": "stage:plan",           "color": "d93f0b", "description": "Stage: Plan"},
    {"name": "stage:build",          "color": "0e8a16", "description": "Stage: Build"},
    {"name": "stage:ship",           "color": "1d76db", "description": "Stage: Ship"},
    {"name": "stage:learn",          "color": "5319e7", "description": "Stage: Verify & Learn"},
    {"name": "blocked",              "color": "b60205", "description": "Has an open failure/blocker"},
    {"name": "awaiting:operator",    "color": "fbca04", "description": "Paused at operator-reserved gate"},
    {"name": "approved:specify",     "color": "0e8a16", "description": "Operator approved: specify"},
    {"name": "approved:jam",         "color": "0e8a16", "description": "Operator approved: jam"},
    {"name": "approved:define-evidence", "color": "0e8a16", "description": "Operator approved: define-evidence"},
    {"name": "approved:merge",       "color": "0e8a16", "description": "Operator approved: merge"},
]

STAGE_LABEL_MAP: dict[str, str] = {
    "align": "stage:align",
    "plan":  "stage:plan",
    "build": "stage:build",
    "ship":  "stage:ship",
    "verify-learn": "stage:learn",
}


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _slug(branch: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "-", branch)[:60]


def _cache_path(branch: str) -> Path:
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    return METRICS_DIR / f"session-{_slug(branch)}-phase.json"


def _load_cache(branch: str) -> dict:
    path = _cache_path(branch)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {
        "branch": branch,
        "issue": None,
        "source": "github",
        "requirement_id": None,
        "pr": None,
        "done": [],
        "failures": [],
        "updated_at": None,
        # v2 event-routing fields (backward compatible — absent in old caches)
        "worktree_path": None,
        "last_signal_cursor": None,
        "delivered_signals": [],
        "pending_event_count": 0,
    }


def _save_cache(branch: str, data: dict) -> None:
    data["updated_at"] = datetime.datetime.utcnow().isoformat() + "Z"
    _cache_path(branch).write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# gh CLI helpers (degrade gracefully without gh)
# ---------------------------------------------------------------------------

def _gh_available() -> bool:
    try:
        r = subprocess.run(["gh", "--version"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def _gh(*args, input_text: str | None = None, dry_run: bool = False) -> tuple[int, str]:
    """Run gh with args. Returns (returncode, stdout+stderr)."""
    cmd = ["gh"] + list(args)
    if dry_run:
        print(f"[dry-run] would run: {' '.join(cmd)}")
        return 0, ""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            input=input_text,
        )
        return proc.returncode, proc.stdout + proc.stderr
    except Exception as e:
        return -1, str(e)


# ---------------------------------------------------------------------------
# Observable-floor detectors  (extension seam — append a row to add a signal)
# ---------------------------------------------------------------------------

def _current_branch() -> str | None:
    """Return the current git branch name, or None if unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=str(REPO_ROOT),
        )
        branch = result.stdout.strip()
        return branch if branch and branch != "HEAD" else None
    except Exception:
        return None


# Paths/extensions that are documentation-only — not considered "implementation" work.
_DOCS_PREFIXES = ("docs/", ".cursor/", "metrics/pr_cost/")
_DOCS_SUFFIXES = (".md", ".txt", ".rst", ".json")


def _has_nondoc_changes() -> bool:
    """Return True when non-documentation files are changed vs origin/main merge-base."""
    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", "origin/main...HEAD"],
            capture_output=True, text=True, timeout=10,
            cwd=str(REPO_ROOT),
        )
        if proc.returncode != 0:
            return False
        files = [f.strip() for f in proc.stdout.splitlines() if f.strip()]
        return any(
            not any(f.startswith(p) for p in _DOCS_PREFIXES)
            and not any(f.endswith(s) for s in _DOCS_SUFFIXES)
            for f in files
        )
    except Exception:
        return False


def _pr_is_open() -> bool:
    """Return True when the current branch has an open PR."""
    if not _gh_available():
        return False
    rc, out = _gh("pr", "view", "--json", "state", "-q", ".state")
    return rc == 0 and out.strip().upper() == "OPEN"


def _pr_is_merged() -> bool:
    """Return True when the current branch's PR is merged."""
    if not _gh_available():
        return False
    rc, out = _gh("pr", "view", "--json", "state", "-q", ".state")
    return rc == 0 and out.strip().upper() == "MERGED"


def _plan_ready_recorded() -> bool:
    """Return True when check_plan_readiness.py stamped plan_ready into the phase cache.

    This is written by check_plan_readiness.py on a passing run, which itself
    requires jam + define-evidence to be recorded.  Once the stamp exists, the
    phase gate enforces that all align substeps (specify, setup, jam, define-evidence)
    are recorded done before the branch can push with a plan.
    """
    branch = _current_branch()
    if not branch:
        return False
    cache_path = _cache_path(branch)
    if not cache_path.exists():
        return False
    try:
        data = json.loads(cache_path.read_text())
        return bool(data.get("plan_ready"))
    except Exception:
        return False


# OBSERVABLE_FLOOR: ordered list of (substep_name, detector_fn).
# Each detector returns True when the real world shows evidence that substep's
# work has happened.  The gate requires every substep before that index to be
# recorded done.  Append one entry to register a new observable signal.
OBSERVABLE_FLOOR: list[tuple[str]] = [
    ("plan",              _plan_ready_recorded), # check_plan_readiness.py stamped plan_ready
    ("implement",         _has_nondoc_changes),  # non-doc code changed vs origin/main
    ("pr-evidence",       _pr_is_open),          # branch has an open PR
    ("post-merge-verify", _pr_is_merged),        # PR was merged
]


# ---------------------------------------------------------------------------
# Issue body helpers
# ---------------------------------------------------------------------------

_STATUS_START = "<!-- phase-state -->"
_STATUS_END   = "<!-- /phase-state -->"


def _build_issue_body(branch: str, requirement: str | None = None) -> str:
    """Build the initial issue body with stage checklists + empty status block."""
    lines = []
    req_line = requirement or branch
    lines.append(f"Work item: **{req_line}**\n")
    lines.append(f"Branch: `{branch}`\n")
    lines.append("")

    for stage in lc.STAGES:
        lines.append(f"### {stage.name.upper()}")
        for sub in stage.substeps:
            driver_tag = "🔒 operator" if sub.driver == "operator" else "🤖 agent"
            lines.append(f"- [ ] `{sub.name}` ({driver_tag})")
        lines.append("")

    # Status block
    lines.append(_STATUS_START)
    lines.append("Current stage: align (1/5) — 0% overall")
    lines.append("Stage progress: 0%")
    lines.append("Done: none")
    lines.append(f"Remaining: {', '.join(s.name for s in lc.all_substeps())}")
    lines.append("Open failures: none")
    lines.append("Summary: work started")
    lines.append(_STATUS_END)

    return "\n".join(lines)


def _rebuild_status_block(data: dict) -> str:
    """Return the updated <!-- phase-state --> block."""
    done_set = set(data.get("done", []))
    failures = data.get("failures", [])
    cur = lc.current_substep(done_set)
    cur_name = cur.name if cur else "complete"
    cur_stage = lc.stage_of(cur_name) if cur else lc.STAGES[-1]

    # Stage index (1-based)
    stage_idx = next((i + 1 for i, s in enumerate(lc.STAGES) if s.name == cur_stage.name), 5)

    overall = lc.overall_pct(done_set)
    s_pct = lc.stage_pct(cur_stage.name, done_set)

    all_names = [s.name for s in lc.all_substeps()]
    remaining = [n for n in all_names if n not in done_set]
    done_list = ", ".join(data.get("done", [])) or "none"
    remaining_list = ", ".join(remaining) or "none"
    fail_list = "; ".join(failures) or "none"

    lines = [
        _STATUS_START,
        f"Current stage: {cur_stage.name} ({stage_idx}/5) — {overall}% overall",
        f"Stage progress: {s_pct}%",
        f"Done: {done_list}",
        f"Remaining: {remaining_list}",
        f"Open failures: {fail_list}",
        f"Summary: {cur_name} {'in progress' if not failures else 'blocked'}",
        _STATUS_END,
    ]
    return "\n".join(lines)


def _update_issue_body(issue_num: int, data: dict, dry_run: bool = False) -> None:
    """Rewrite the status block and tick done substeps in the issue body."""
    if not _gh_available():
        return
    rc, body = _gh("issue", "view", str(issue_num), "--json", "body", "-q", ".body")
    if rc != 0 or not body:
        return

    # Tick checkboxes for done substeps
    done_set = set(data.get("done", []))
    for sub_name in done_set:
        body = re.sub(
            rf"- \[ \] `{re.escape(sub_name)}`",
            f"- [x] `{sub_name}`",
            body,
        )
    # Update status block
    new_status = _rebuild_status_block(data)
    if _STATUS_START in body and _STATUS_END in body:
        body = re.sub(
            rf"{re.escape(_STATUS_START)}[\s\S]*?{re.escape(_STATUS_END)}",
            new_status,
            body,
        )
    else:
        body += "\n" + new_status

    _gh("issue", "edit", str(issue_num), "--body", body, dry_run=dry_run)


def _apply_kickoff(issue_num: int, branch: str, data: dict, *, dry_run: bool = False) -> None:
    """Idempotently wire a tracking issue: labels + kickoff state + checklist body.

    Called from both the create-new and link-existing paths of cmd_init so they
    can never drift.  --add-label is a no-op if the label is already present, so
    re-running is safe.
    """
    if dry_run:
        print(f"[dry-run] would wire issue #{issue_num}: labels + kickoff + body")
        return
    if not _gh_available():
        return
    _gh("issue", "edit", str(issue_num),
        "--add-label", "jarvis-work", "--add-label", "stage:align")
    # specify (operator typed the requirement) + setup (worktree/issue exists)
    # are factually complete the moment we link the issue.
    if not data.get("done"):
        data["done"] = ["specify", "setup"]
    # Skip body update when the status block is already present (idempotent).
    rc, existing_body = _gh("issue", "view", str(issue_num), "--json", "body", "-q", ".body")
    if rc == 0 and _STATUS_START in existing_body:
        return
    _update_issue_body(issue_num, data, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Sub-commands
# ---------------------------------------------------------------------------

def cmd_ensure_labels(args) -> int:
    """Idempotently create all Jarvis work-tracking labels."""
    if not _gh_available():
        print("WARNING: gh not available; cannot create labels.", file=sys.stderr)
        return 0
    for label in ALL_LABELS:
        rc, out = _gh(
            "label", "create", label["name"],
            "--color", label["color"],
            "--description", label["description"],
            "--force",
        )
        status = "ok" if rc == 0 else f"warn(rc={rc})"
        print(f"  label {label['name']!r}: {status}")
    return 0


def _ensure_issue_tracked(issue_num: int, branch: str, *, requirement: str | None = None) -> None:
    """Idempotently make a manually-filed issue fully lifecycle-trackable.

    When ``--issue N`` links an existing issue that was created outside of
    new_requirement.py, it may lack the ``jarvis-work``/``stage:align`` labels
    and the ``<!-- phase-state -->`` checklist body that phase_state's helpers
    (report, gate, advance) expect.  This function adds them if absent.

    Safe to call multiple times: label add is a no-op when the label is already
    present; body injection is skipped when the status block already exists.
    """
    # Ensure required labels
    _gh("issue", "edit", str(issue_num), "--add-label", "jarvis-work,stage:align")

    # Inject checklist body only when the status sentinel is absent
    rc, body = _gh("issue", "view", str(issue_num), "--json", "body", "-q", ".body")
    if rc != 0:
        return
    if _STATUS_START in body:
        return  # already has the tracking block — idempotent

    # Append the phase checklist + status block to whatever body the issue had
    checklist = _build_issue_body(branch, requirement=requirement)
    new_body = (body.rstrip() + "\n\n" + checklist) if body.strip() else checklist
    _gh("issue", "edit", str(issue_num), "--body", new_body)


def cmd_init(args) -> int:
    """Create a GitHub issue for this branch (idempotent)."""
    branch = args.branch
    dry_run = getattr(args, "dry_run", False)
    data = _load_cache(branch)

    # Idempotency: if we already have an issue, don't create another
    if data.get("issue") and not args.issue:
        print(f"Issue #{data['issue']} already exists for branch {branch!r}.")
        return 0

    issue_num = args.issue or data.get("issue")

    requirement = getattr(args, "requirement", None)
    kickoff = getattr(args, "kickoff", False)

    if args.requirement_id:
        data["requirement_id"] = args.requirement_id
    if requirement:
        data["requirement"] = requirement

    if issue_num:
        data["issue"] = issue_num
        # Apply labels + kickoff state + body to GitHub (idempotent).
        # Previously this path only wrote the local cache, causing the "no labels
        # on pre-filed issues" bug (root cause of #86 wiring miss).
        _apply_kickoff(issue_num, branch, data, dry_run=dry_run)
        _save_cache(branch, data)
        print(f"Linked to existing issue #{issue_num} for branch {branch!r}.")
        if not dry_run and _gh_available():
            _ensure_issue_tracked(issue_num, branch, requirement=requirement)
        return 0

    # Create the issue
    title = f"[work] {requirement[:60]}" if requirement else f"[work] {branch}"
    body = _build_issue_body(branch, requirement=requirement)
    labels = "jarvis-work,stage:align"

    if dry_run:
        print(f"[dry-run] would run: gh issue create --title {title!r} --label {labels}")
        if kickoff:
            print("[dry-run] would seed done=[specify, setup] (--kickoff)")
        print(f"[dry-run] body (excerpt):\n{body[:300]}…")
        return 0

    if not _gh_available():
        print("WARNING: gh not available; saving cache only.", file=sys.stderr)
        _save_cache(branch, data)
        return 0

    rc, out = _gh("issue", "create",
                  "--title", title,
                  "--body", body,
                  "--label", labels)
    if rc != 0:
        print(f"ERROR: gh issue create failed: {out}", file=sys.stderr)
        return 1

    # Extract issue number + URL from output
    match = re.search(r"(https://github\.com/\S+/issues/(\d+))", out)
    if match:
        data["issue_url"] = match.group(1)
        data["issue"] = int(match.group(2))
        if kickoff:
            # --kickoff seeds done=[specify,setup]; _apply_kickoff below also
            # sets them when done is empty, but honor the explicit flag here.
            data["done"] = ["specify", "setup"]
        _apply_kickoff(data["issue"], branch, data, dry_run=dry_run)
        _save_cache(branch, data)
        print(f"Created issue #{data['issue']} for branch {branch!r}.")
        print(f"Tracking issue → {data['issue_url']}")
    else:
        print(f"Created issue (could not parse number): {out.strip()}")

    return 0


def cmd_advance(args) -> int:
    """Advance the branch to the next substep."""
    branch = args.branch
    to_substep = args.to
    operator_approved = getattr(args, "operator_approved", False)
    data = _load_cache(branch)
    done_set = set(data.get("done", []))

    # Validate substep name
    try:
        target_idx = lc.substep_index(to_substep)
    except ValueError:
        print(f"ERROR: unknown substep {to_substep!r}.", file=sys.stderr)
        print(f"Valid substeps: {[s.name for s in lc.all_substeps()]}", file=sys.stderr)
        return 1

    # Forward-only check
    if to_substep in done_set:
        print(f"Substep {to_substep!r} is already done.", file=sys.stderr)
        return 1

    # Ensure previous substeps are done (no skipping)
    steps = lc.all_substeps()
    for prev in steps[:target_idx]:
        if prev.name not in done_set:
            print(f"ERROR: cannot advance to {to_substep!r} — substep {prev.name!r} is not done yet.",
                  file=sys.stderr)
            return 1

    # Operator-gate enforcement
    target_sub = steps[target_idx]
    if target_sub.driver == "operator" and not operator_approved:
        issue_num = data.get("issue")
        print(
            f"ERROR: substep {to_substep!r} is operator-reserved. "
            f"Operator approval is given in the working chat; once approved, "
            f"re-run with --operator-approved [--note '<summary>'].",
            file=sys.stderr,
        )
        # Mirror the pause to the issue as a read-only audit trail (no action required from
        # the operator on GitHub — approval happens exclusively in the Cursor chat).
        if issue_num and _gh_available():
            _gh("issue", "comment", str(issue_num),
                "--body",
                f"Paused at operator gate **{to_substep}** — "
                f"approval is handled in the working Cursor chat.")
            _gh("issue", "edit", str(issue_num), "--add-label", "awaiting:operator")
        return 1

    # Mark done, determine stage transition
    prev_stage = lc.stage_of(steps[target_idx - 1].name) if target_idx > 0 else lc.STAGES[0]
    data["done"].append(to_substep)
    done_set.add(to_substep)
    _save_cache(branch, data)

    # Update issue — mirror the approval + clear awaiting:operator
    issue_num = data.get("issue")
    note = getattr(args, "note", None)
    if issue_num and _gh_available():
        new_stage = lc.stage_of(to_substep)
        # Stamp approval: add approved:<substep> label and post a provenance comment.
        _gh("issue", "edit", str(issue_num), "--add-label", f"approved:{to_substep}")
        approval_body = f"Operator approved **{to_substep}** in the working Cursor chat."
        if note:
            approval_body += f"\n\n{note}"
        _gh("issue", "comment", str(issue_num), "--body", approval_body)
        # Swap stage label if we crossed a stage boundary
        if new_stage.name != prev_stage.name:
            old_label = STAGE_LABEL_MAP.get(prev_stage.name)
            new_label = STAGE_LABEL_MAP.get(new_stage.name)
            if old_label:
                _gh("issue", "edit", str(issue_num), "--remove-label", old_label)
            if new_label:
                _gh("issue", "edit", str(issue_num), "--add-label", new_label)
        # Clear awaiting:operator
        _gh("issue", "edit", str(issue_num), "--remove-label", "awaiting:operator")
        _update_issue_body(issue_num, data)

    overall = lc.overall_pct(done_set)
    print(f"Advanced to {to_substep!r} — {overall}% overall.")
    return 0


def cmd_fail(args) -> int:
    """Record a failure/blocker."""
    branch = args.branch
    reason = args.reason
    data = _load_cache(branch)
    data.setdefault("failures", [])
    data["failures"].append(reason)
    _save_cache(branch, data)

    issue_num = data.get("issue")
    if issue_num and _gh_available():
        _gh("issue", "edit", str(issue_num), "--add-label", "blocked")
        _update_issue_body(issue_num, data)

    print(f"Recorded failure for branch {branch!r}: {reason}")
    return 0


def cmd_clear_fail(args) -> int:
    """Clear failures/blockers."""
    branch = args.branch
    data = _load_cache(branch)
    data["failures"] = []
    _save_cache(branch, data)

    issue_num = data.get("issue")
    if issue_num and _gh_available():
        _gh("issue", "edit", str(issue_num), "--remove-label", "blocked")
        _update_issue_body(issue_num, data)

    print(f"Cleared failures for branch {branch!r}.")
    return 0


def cmd_status(args) -> int:
    """Print status for a branch (or the current branch if --branch not given)."""
    branch = args.branch
    if not branch:
        try:
            branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        except Exception:
            branch = None
    if not branch:
        print("ERROR: --branch required or must be in a git repo.", file=sys.stderr)
        return 1

    data = _load_cache(branch)
    done_set = set(data.get("done", []))
    failures = data.get("failures", [])
    cur = lc.current_substep(done_set)
    cur_stage = lc.stage_of(cur.name) if cur else lc.STAGES[-1]
    overall = lc.overall_pct(done_set)
    s_pct = lc.stage_pct(cur_stage.name, done_set)
    remaining = [s.name for s in lc.all_substeps() if s.name not in done_set]

    if getattr(args, "json", False):
        print(json.dumps({
            "branch": branch,
            "issue": data.get("issue"),
            "current_stage": cur_stage.name,
            "current_substep": cur.name if cur else None,
            "overall_pct": overall,
            "stage_pct": s_pct,
            "done": data.get("done", []),
            "remaining": remaining,
            "failures": failures,
        }, indent=2))
        return 0

    print(f"Branch:  {branch}")
    print(f"Issue:   #{data.get('issue') or 'none'}")
    print(f"Worktree: {data.get('worktree_path') or '(not set)'}")
    print(f"Pending events: {data.get('pending_event_count', 0)}")
    print(f"Stage:   {cur_stage.name}  ({s_pct}% of stage)")
    print(f"Overall: {overall}%")
    print(f"Substep: {cur.name if cur else 'complete'}")
    print(f"Remaining ({len(remaining)}): {', '.join(remaining) or 'none'}")
    if failures:
        print(f"Failures: {'; '.join(failures)}")
    return 0


def cmd_report(args) -> int:
    """List all in-flight work items (from GitHub Issues + local caches)."""
    rows: list[dict] = []

    # Pull from GitHub if available
    if _gh_available():
        rc, out = _gh("issue", "list", "--label", "jarvis-work",
                      "--json", "number,title,labels,state",
                      "--state", "open", "--limit", "50")
        if rc == 0 and out.strip():
            try:
                issues = json.loads(out)
                for iss in issues:
                    stage = next(
                        (lbl["name"].replace("stage:", "") for lbl in iss.get("labels", [])
                         if lbl["name"].startswith("stage:")),
                        "unknown"
                    )
                    blocked = any(lbl["name"] == "blocked" for lbl in iss.get("labels", []))
                    rows.append({
                        "issue": iss["number"],
                        "title": iss["title"][:50],
                        "stage": stage,
                        "pct": "?",
                        "blocked": "YES" if blocked else "",
                    })
            except Exception:
                pass

    # Supplement with local caches
    local_issues: dict[int, dict] = {}
    if METRICS_DIR.exists():
        for path in METRICS_DIR.glob("*-phase.json"):
            try:
                data = json.loads(path.read_text())
                issue_num = data.get("issue")
                if not issue_num:
                    continue
                done_set = set(data.get("done", []))
                local_issues[issue_num] = {
                    "pct": lc.overall_pct(done_set),
                    "failures": data.get("failures", []),
                    "worktree_path": data.get("worktree_path"),
                    "pending_event_count": data.get("pending_event_count", 0),
                }
            except Exception:
                pass
    # Merge local pct into rows
    for row in rows:
        local = local_issues.get(row["issue"])
        if local:
            row["pct"] = f"{local['pct']}%"
            if local["failures"]:
                row["blocked"] = "YES"
            row["worktree"] = local.get("worktree_path") or ""
            row["pending"] = local.get("pending_event_count", 0)

    if not rows:
        print("No open jarvis-work issues found.")
        return 0

    print(f"\n{'#':<6} {'Title':<50} {'Stage':<14} {'%':<6} {'Pending':<8} {'Blocked'}")
    print("─" * 95)
    for row in rows:
        pending = str(row.get("pending", 0)) if row.get("pending", 0) else ""
        print(f"  {row['issue']:<4} {row['title']:<50} {row['stage']:<14} {str(row['pct']):<6} {pending:<8} {row['blocked']}")
        if row.get("worktree"):
            import os
            wt = row["worktree"]
            wt_short = "…" + wt[-60:] if len(wt) > 63 else wt
            print(f"        Worktree: {wt_short}")
    return 0


def cmd_gate(args) -> int:
    """Phase-consistency gate: observable progress may not outrun recorded progress.

    For every substep whose index is less than the highest observed substep (per
    OBSERVABLE_FLOOR detectors), that substep must be recorded done in the phase
    cache.  This makes the whole lifecycle ladder non-bypassable: an agent cannot
    push code, open a PR, or merge without the preceding substeps (including operator
    gates) being on record.

    Exits 0 when:
      - Branch is not lifecycle-tracked (no cache) — untracked branches are ignored.
      - No observable artifacts detected yet — nothing to enforce.
      - All substeps before the highest observed one are recorded done.

    Exits 1 when observable progress outran recorded progress, printing the exact
    `phase_state.py advance` commands needed to catch up (in order).
    """
    branch = getattr(args, "branch", None) or _current_branch()
    if not branch:
        print("WARNING: could not determine branch; skipping phase gate.", file=sys.stderr)
        return 0

    cache_path = _cache_path(branch)
    if not cache_path.exists():
        print(f"[phase-gate] branch {branch!r} not lifecycle-tracked — skipping.")
        return 0

    data = _load_cache(branch)
    done_set = set(data.get("done", []))
    all_steps = lc.all_substeps()

    # G2 drift check: if the branch has a linked tracking issue but that issue
    # is missing its stage:* label on GitHub, the link-wiring step was skipped
    # (root cause of the #86 miss).  Fail fast with a clear fix command.
    issue_num = data.get("issue")
    if issue_num and _gh_available():
        rc, out = _gh("issue", "view", str(issue_num),
                      "--json", "labels",
                      "-q", "[.labels[].name] | join(\",\")")
        if rc == 0 and "stage:" not in out:
            print(
                f"\nPHASE GATE FAILED — issue #{issue_num} (branch {branch!r}) "
                f"has no stage:* label — incomplete link.\n"
                f"Fix: python3 scripts/phase_state.py init --branch {branch} --issue {issue_num}\n",
                file=sys.stderr,
            )
            return 1

    # Find the highest observed substep index from live detectors
    observed_idx = -1
    observed_name = None
    for sub_name, detector in OBSERVABLE_FLOOR:
        try:
            if detector():
                idx = lc.substep_index(sub_name)
                if idx > observed_idx:
                    observed_idx = idx
                    observed_name = sub_name
        except Exception:
            pass  # degrade gracefully if detector fails

    if observed_idx < 0:
        print(f"[phase-gate] branch {branch!r}: no observable artifacts yet — OK.")
        return 0

    # Every substep before observed_idx must be done
    missing = [s for s in all_steps[:observed_idx] if s.name not in done_set]

    if not missing:
        print(f"[phase-gate] branch {branch!r}: ladder consistent up to "
              f"'{observed_name}' — OK.")
        return 0

    print(
        f"\nPHASE GATE FAILED — branch {branch!r}\n"
        f"Observable world shows work at '{observed_name}', but these substep(s)\n"
        f"are not recorded done in the phase cache:\n",
        file=sys.stderr,
    )
    for s in missing:
        if s.driver == "operator":
            print(
                f"  • {s.name!r} [operator-reserved] — obtain approval in chat, then run:\n"
                f"    python3 scripts/phase_state.py advance --branch {branch}"
                f" --to {s.name} --operator-approved [--note '<summary>']",
                file=sys.stderr,
            )
        else:
            print(
                f"  • {s.name!r} [agent] — record via:\n"
                f"    python3 scripts/phase_state.py advance --branch {branch}"
                f" --to {s.name}",
                file=sys.stderr,
            )
    print(
        f"\nRecord the missing substeps (in order) then re-run `verify.py --full`.\n"
        f"The phase gate prevents shipping work that outran the lifecycle ladder.",
        file=sys.stderr,
    )
    return 1


def _observed_floor() -> tuple[int, str | None]:
    """Highest substep the real world shows evidence for (per OBSERVABLE_FLOOR)."""
    observed_idx = -1
    observed_name = None
    for sub_name, detector in OBSERVABLE_FLOOR:
        try:
            if detector():
                idx = lc.substep_index(sub_name)
                if idx > observed_idx:
                    observed_idx = idx
                    observed_name = sub_name
        except Exception:
            pass  # degrade gracefully if a detector fails
    return observed_idx, observed_name


def cmd_drift_check(args) -> int:
    """Advisory phase-drift nudge (obs 1) — always exits 0.

    Sibling of cmd_gate, but *inclusive* of the observed substep and non-blocking.
    When observable progress (plan file, non-doc changes, open PR, merge) has
    outrun the recorded ``done`` list, prints the exact ``advance --to <substep>``
    commands so the agent records phases *as each substep completes* instead of
    batching them all just before the PR. Wired into drain.sh's idle followup.

    Prints nothing (and exits 0) when the branch is untracked or already in sync,
    so it is safe to call unconditionally on every idle turn.
    """
    branch = getattr(args, "branch", None) or _current_branch()
    if not branch or not _cache_path(branch).exists():
        return 0

    data = _load_cache(branch)
    done_set = set(data.get("done", []))
    all_steps = lc.all_substeps()

    observed_idx, observed_name = _observed_floor()
    if observed_idx < 0:
        return 0

    # Inclusive of the observed substep: if the world shows 'implement' evidence,
    # 'implement' itself should be on record too (the gate only enforces priors).
    missing = [s for s in all_steps[: observed_idx + 1] if s.name not in done_set]
    if not missing:
        return 0

    print(
        f"PHASE DRIFT — branch {branch!r}: observable work reached "
        f"'{observed_name}' but these substep(s) are not recorded yet. "
        f"Advance them now, in order (don't batch until PR time):"
    )
    for s in missing:
        if s.driver == "operator":
            print(
                f"  python3 scripts/phase_state.py advance --branch {branch} "
                f"--to {s.name} --operator-approved --note '<approval summary>'"
            )
        else:
            print(
                f"  python3 scripts/phase_state.py advance --branch {branch} --to {s.name}"
            )
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Work-phase tracker backed by GitHub Issues."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ensure-labels", help="Idempotently create all tracking labels")

    p_init = sub.add_parser("init", help="Create/link a GitHub issue for this branch")
    p_init.add_argument("--branch", required=True)
    p_init.add_argument("--requirement", help="Requirement text for the issue title + body")
    p_init.add_argument("--kickoff", action="store_true",
                        help="Seed done=[specify,setup] (both complete the moment new_requirement runs)")
    p_init.add_argument("--requirement-id", type=int)
    p_init.add_argument("--issue", type=int, help="Link to an existing issue instead of creating")
    p_init.add_argument("--source", default="github", choices=["github", "linear", "jira"])
    p_init.add_argument("--dry-run", action="store_true")

    p_adv = sub.add_parser("advance", help="Advance the branch to a substep")
    p_adv.add_argument("--branch", required=True)
    p_adv.add_argument("--to", required=True, metavar="SUBSTEP")
    p_adv.add_argument("--operator-approved", action="store_true",
                       help="Bypass the operator-gate check (use only after actual approval in chat)")
    p_adv.add_argument("--note", default=None,
                       help="Free-text summary of the in-chat approval/jam; mirrored to the issue.")

    p_fail = sub.add_parser("fail", help="Record a failure/blocker")
    p_fail.add_argument("--branch", required=True)
    p_fail.add_argument("--reason", required=True)

    p_clr = sub.add_parser("clear-fail", help="Clear failures/blockers")
    p_clr.add_argument("--branch", required=True)

    p_status = sub.add_parser("status", help="Print status for a branch")
    p_status.add_argument("--branch", default=None)
    p_status.add_argument("--json", action="store_true")

    sub.add_parser("report", help="List all open work items")

    p_gate = sub.add_parser("gate", help="Phase-consistency gate (used by verify.py --full)")
    p_gate.add_argument("--branch", default=None,
                        help="Branch to check (default: current git branch)")

    p_drift = sub.add_parser("drift-check",
                             help="Advisory nudge to advance phases as work completes (non-blocking)")
    p_drift.add_argument("--branch", default=None,
                         help="Branch to check (default: current git branch)")

    args = parser.parse_args(argv)

    dispatch = {
        "ensure-labels": cmd_ensure_labels,
        "init":          cmd_init,
        "advance":       cmd_advance,
        "fail":          cmd_fail,
        "clear-fail":    cmd_clear_fail,
        "status":        cmd_status,
        "report":        cmd_report,
        "gate":          cmd_gate,
        "drift-check":   cmd_drift_check,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
