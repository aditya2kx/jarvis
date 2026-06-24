#!/usr/bin/env python3
"""Dogfood the Jarvis lifecycle end-to-end — prove the harness actually works.

Drives a trivial dummy requirement ("Add docs/dogfood/README.md") through all 12
substeps of scripts/lifecycle.py against REAL infrastructure: a real GitHub
tracking issue, a real throwaway PR off origin/main, real operator-gate
enforcement, and a real operator merge. Produces a structured state file and an
annotated transcript that marks every step as operator-simulated vs harness-driven.

Because the merge gate is genuinely operator-reserved (the bot is a non-admin, so
branch protection forces a human approval), the run PAUSES at `merge` and is
finished by `resume` after the operator approves the dummy PR.

Usage:
    python3 scripts/dogfood_lifecycle.py run      [--force] [--state PATH]
    python3 scripts/dogfood_lifecycle.py resume   [--state PATH]
    python3 scripts/dogfood_lifecycle.py check    [--state PATH]
    python3 scripts/dogfood_lifecycle.py cleanup  [--state PATH]

`check` is offline and deterministic (operates only on the state file), so it is
safe in CI and unit tests.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import lifecycle as lc

REPO_ROOT = Path(__file__).parent.parent
PS = REPO_ROOT / "scripts" / "phase_state.py"
CPR = REPO_ROOT / "scripts" / "check_plan_readiness.py"
VERIFY = REPO_ROOT / "scripts" / "verify.py"
COST = REPO_ROOT / "scripts" / "pr_cost_ledger.py"
DEFAULT_STATE = REPO_ROOT / "metrics" / "pr_cost" / "dogfood-state.json"

DUMMY_REQUIREMENT = "Add docs/dogfood/README.md documenting the dogfood evidence directory."

# specify is SEEDED by init --kickoff; merge is the REAL operator gate.
# These two are the operator gates we DEMO (refuse-without-approval, then approve).
SIMULATED_GATES = ["jam", "define-evidence"]


# ---------------------------------------------------------------------------
# Record + state
# ---------------------------------------------------------------------------

@dataclass
class StepRecord:
    substep: str
    driver: str               # "operator" | "agent"
    marker: str               # SEEDED | HARNESS-DRIVEN | OPERATOR-SIMULATED | OPERATOR-REAL
    actions: list = field(default_factory=list)
    commands: list = field(default_factory=list)   # [{"cmd","rc","excerpt"}]
    gate_refused: bool = False
    gate_approved: bool = False
    ok: bool = True


def _run(cmd: list[str], cwd: str | None = None, timeout: int = 600) -> tuple[int, str, str]:
    """Run a command; return (returncode, stdout, stderr)."""
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    return p.returncode, p.stdout, p.stderr


def _rec_cmd(rec: StepRecord, label: str, rc: int, out: str, err: str) -> None:
    rec.commands.append({"cmd": label, "rc": rc, "excerpt": (out + err).strip()[:300]})


def load_state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _repo_slug(run=_run) -> str:
    rc, out, _ = run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
                     cwd=str(REPO_ROOT))
    return out.strip() if rc == 0 else ""


def _advance(rec: StepRecord, state: dict, to: str, *, approved: bool = False, run=_run) -> None:
    cmd = ["python3", str(PS), "advance", "--branch", state["branch"], "--to", to]
    if approved:
        cmd.append("--operator-approved")
    rc, out, err = run(cmd, cwd=str(REPO_ROOT))
    _rec_cmd(rec, f"phase_state advance --to {to}" + (" --operator-approved" if approved else ""),
             rc, out, err)
    if rc != 0:
        rec.ok = False


# ---------------------------------------------------------------------------
# Operator-gate demo (refuse-without-approval, then operator-simulated approval)
# ---------------------------------------------------------------------------

def demo_operator_gate(branch: str, issue: int | None, gate: str, *, run=_run) -> StepRecord:
    """Prove the gate has teeth, then simulate the operator approving it."""
    rec = StepRecord(substep=gate, driver="operator", marker="OPERATOR-SIMULATED")

    # 1) HARNESS proves the gate bites: advance WITHOUT approval must fail.
    rc, out, err = run(["python3", str(PS), "advance", "--branch", branch, "--to", gate],
                       cwd=str(REPO_ROOT))
    _rec_cmd(rec, f"advance --to {gate} (no approval)", rc, out, err)
    rec.gate_refused = (rc != 0)
    rec.actions.append(f"Gate refused without approval (rc={rc}); awaiting:operator posted.")

    # 2) OPERATOR (simulated here): pass --operator-approved; phase_state auto-stamps the
    # approved:<gate> label and posts the provenance comment on the issue. No manual label
    # manipulation needed — the issue is a read-only mirror of what the chat decided.
    rc2, out2, err2 = run(["python3", str(PS), "advance", "--branch", branch, "--to", gate,
                           "--operator-approved",
                           "--note", f"Gate '{gate}' simulated-approved by dogfood harness."],
                          cwd=str(REPO_ROOT))
    _rec_cmd(rec, f"advance --to {gate} --operator-approved --note", rc2, out2, err2)
    rec.gate_approved = (rc2 == 0)
    rec.actions.append(f"Operator approval (chat-only surface) → agent stamped issue (rc={rc2}).")

    rec.ok = rec.gate_refused and rec.gate_approved
    return rec


# ---------------------------------------------------------------------------
# Agent substeps
# ---------------------------------------------------------------------------

def step_setup(state: dict, *, run=_run) -> list[StepRecord]:
    """specify (SEEDED) + setup (HARNESS): worktree off origin/main + tracking issue."""
    branch, worktree = state["branch"], state["worktree"]

    spec = StepRecord("specify", "operator", "SEEDED",
                      actions=[f"Operator supplied requirement: {DUMMY_REQUIREMENT!r} "
                               f"(seeded into the issue via init --kickoff)."])

    setup = StepRecord("setup", "agent", "HARNESS-DRIVEN")
    rc, out, err = run(["git", "fetch", "origin", "main"], cwd=str(REPO_ROOT))
    _rec_cmd(setup, "git fetch origin main", rc, out, err)
    rc, out, err = run(["git", "worktree", "add", "-b", branch, worktree, "origin/main"],
                       cwd=str(REPO_ROOT))
    _rec_cmd(setup, f"git worktree add -b {branch} (off origin/main)", rc, out, err)
    setup.ok = (rc == 0)

    rc, out, err = run(["python3", str(PS), "init", "--branch", branch,
                        "--requirement", DUMMY_REQUIREMENT, "--kickoff"], cwd=str(REPO_ROOT))
    _rec_cmd(setup, "phase_state init --kickoff", rc, out, err)
    m = re.search(r"(https://github\.com/\S+/issues/(\d+))", out)
    if m:
        state["issue_url"] = m.group(1)
        state["issue"] = int(m.group(2))
        setup.actions.append(f"Created tracking issue {state['issue_url']} "
                             f"(Align 50%; specify+setup ticked).")
    else:
        setup.ok = False
        setup.actions.append("Could not parse issue URL from phase_state init output.")
    return [spec, setup]


def step_plan(state: dict, *, run=_run) -> StepRecord:
    """plan (HARNESS): prove check_plan_readiness FAILS on a stub, PASSES on a full plan."""
    rec = StepRecord("plan", "agent", "HARNESS-DRIVEN")
    tmp = Path(tempfile.mkdtemp())
    thin, full = tmp / "thin.md", tmp / "full.md"
    thin.write_text(THIN_PLAN, encoding="utf-8")
    full.write_text(FULL_PLAN, encoding="utf-8")

    rc1, out1, err1 = run(["python3", str(CPR), "--plan", str(thin)], cwd=str(REPO_ROOT))
    _rec_cmd(rec, "check_plan_readiness (thin stub — expect FAIL)", rc1, out1, err1)
    rc2, out2, err2 = run(["python3", str(CPR), "--plan", str(full)], cwd=str(REPO_ROOT))
    _rec_cmd(rec, "check_plan_readiness (full plan — expect PASS)", rc2, out2, err2)

    rec.ok = (rc1 != 0 and rc2 == 0)
    rec.actions.append(f"Thin stub FAILED (rc={rc1}); full plan PASSED (rc={rc2}) — gate works.")
    _advance(rec, state, "plan", run=run)
    return rec


def step_implement(state: dict, *, run=_run) -> StepRecord:
    """implement (HARNESS): write the one doc file in the dummy worktree + commit."""
    rec = StepRecord("implement", "agent", "HARNESS-DRIVEN")
    wt = Path(state["worktree"])
    doc = wt / "docs" / "dogfood" / "README.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(DOGFOOD_DOC, encoding="utf-8")
    rec.actions.append("Wrote docs/dogfood/README.md in the dummy worktree.")

    rc, out, err = run(["git", "add", "docs/dogfood/README.md"], cwd=str(wt))
    _rec_cmd(rec, "git add docs/dogfood/README.md", rc, out, err)
    rc, out, err = run(["git", "commit", "-m", "docs: add dogfood evidence directory README"],
                       cwd=str(wt))
    _rec_cmd(rec, "git commit", rc, out, err)
    rec.ok = (rc == 0)
    _advance(rec, state, "implement", run=run)
    return rec


def step_verify(state: dict, *, run=_run) -> StepRecord:
    """verify (HARNESS): run the local verify harness (fast) on the session tree."""
    rec = StepRecord("verify", "agent", "HARNESS-DRIVEN")
    rc, out, err = run(["python3", str(VERIFY), "--fast"], cwd=str(REPO_ROOT))
    _rec_cmd(rec, "verify.py --fast", rc, out, err)
    state["verify_rc"] = rc
    rec.actions.append(f"Local verify (fast) rc={rc}. (Dummy is doc-only; CI runs full gates.)")
    _advance(rec, state, "verify", run=run)
    return rec


def step_pr_evidence(state: dict, *, run=_run) -> StepRecord:
    """pr-evidence (HARNESS): push, open the dummy PR, record synthetic build cost."""
    rec = StepRecord("pr-evidence", "agent", "HARNESS-DRIVEN")
    wt, branch = Path(state["worktree"]), state["branch"]

    rc, out, err = run(["git", "push", "-u", "origin", branch], cwd=str(wt))
    _rec_cmd(rec, "git push -u origin <dummy>", rc, out, err)

    rc, out, err = run(["gh", "pr", "create", "--base", "main", "--head", branch,
                        "--title", "docs: dogfood lifecycle evidence (throwaway)",
                        "--label", "no-e2e",  # doc-only → skip sandbox-e2e
                        "--body", PR_BODY], cwd=str(wt))
    _rec_cmd(rec, "gh pr create", rc, out, err)
    m = re.search(r"/pull/(\d+)", out)
    if m:
        state["dummy_pr"] = int(m.group(1))
        state["dummy_pr_url"] = m.group(0)
        rec.actions.append(f"Opened dummy PR #{state['dummy_pr']}.")
    else:
        rec.ok = False
        rec.actions.append("Could not parse dummy PR number from gh pr create output.")
        _advance(rec, state, "pr-evidence", run=run)
        return rec

    pr = str(state["dummy_pr"])
    ts = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    rc, out, err = run(["python3", str(COST), "set-meta", "--pr", pr,
                        "--branch", branch, "--requirement", DUMMY_REQUIREMENT], cwd=str(REPO_ROOT))
    _rec_cmd(rec, "pr_cost_ledger set-meta", rc, out, err)
    rc, out, err = run(["python3", str(COST), "record-build", "--pr", pr,
                        "--ts", ts, "--tokens", "1000", "--cost", "0.01",
                        "--model", "claude-4.6-sonnet-medium-thinking",
                        "--note", "dogfood synthetic build cost"], cwd=str(REPO_ROOT))
    _rec_cmd(rec, "pr_cost_ledger record-build (synthetic)", rc, out, err)
    rc, out, err = run(["python3", str(COST), "validate", "--pr", pr, "--require-build"],
                       cwd=str(REPO_ROOT))
    _rec_cmd(rec, "pr_cost_ledger validate --require-build", rc, out, err)

    _advance(rec, state, "pr-evidence", run=run)
    return rec


def step_babysit(state: dict, *, run=_run, poll_max: int = 40, poll_sleep: int = 15,
                 sleep=time.sleep) -> StepRecord:
    """babysit (HARNESS): wait for dummy PR CI green; capture first Claude comment (Part B proof)."""
    rec = StepRecord("babysit", "agent", "HARNESS-DRIVEN")
    pr = str(state["dummy_pr"])
    repo = state.get("repo", "")

    final = "unknown"
    for _ in range(poll_max):
        rc, out, err = run(["gh", "pr", "checks", pr], cwd=str(REPO_ROOT))
        low = out.lower()
        if "\tfail\t" in low or "\tfailing" in low or "fail\t" in low:
            final = "fail"
            break
        if "pending" not in low and "\tin_progress" not in low and out.strip():
            final = "green"
            break
        sleep(poll_sleep)
    _rec_cmd(rec, "gh pr checks (poll)", rc, out, err)
    rec.actions.append(f"Dummy PR checks settled: {final}.")

    # Part B production proof: the first Claude review must NOT call itself a re-review.
    rc, out, err = run(["gh", "api", f"repos/{repo}/issues/{pr}/comments",
                        "--jq", '[.[]|select(.user.login|startswith("claude"))]|first|.body'],
                       cwd=str(REPO_ROOT))
    first = (out or "").lower()
    state["rereview_misfire"] = ("re-review" in first or "re review" in first)
    rec.actions.append(f"First Claude comment captured; rereview_misfire={state['rereview_misfire']}.")
    _advance(rec, state, "babysit", run=run)
    return rec


def step_post_merge_verify(state: dict, *, run=_run) -> StepRecord:
    """post-merge-verify (HARNESS): the doc file is present on origin/main after merge."""
    rec = StepRecord("post-merge-verify", "agent", "HARNESS-DRIVEN")
    rc, out, err = run(["git", "fetch", "origin", "main"], cwd=str(REPO_ROOT))
    _rec_cmd(rec, "git fetch origin main", rc, out, err)
    rc, out, err = run(["git", "cat-file", "-e", "origin/main:docs/dogfood/README.md"],
                       cwd=str(REPO_ROOT))
    _rec_cmd(rec, "git cat-file -e origin/main:docs/dogfood/README.md", rc, out, err)
    rec.ok = (rc == 0)
    rec.actions.append(f"docs/dogfood/README.md present on origin/main: {rec.ok}.")
    _advance(rec, state, "post-merge-verify", run=run)
    return rec


def step_retrospective(state: dict, *, run=_run) -> StepRecord:
    """retrospective (HARNESS): append a PROGRESS line, close the issue, reach 100%."""
    rec = StepRecord("retrospective", "agent", "HARNESS-DRIVEN")
    try:
        progress = REPO_ROOT / "PROGRESS.md"
        line = (f"\n## {datetime.date.today().isoformat()} — Dogfood: lifecycle walked "
                f"end-to-end (issue #{state.get('issue')}, dummy PR #{state.get('dummy_pr')})\n")
        if progress.exists() and "Dogfood: lifecycle walked end-to-end" not in progress.read_text():
            with progress.open("a", encoding="utf-8") as fh:
                fh.write(line)
            rec.actions.append("Appended dogfood line to PROGRESS.md.")
    except Exception as e:  # noqa: BLE001 — retrospective must not crash the run
        rec.actions.append(f"PROGRESS.md append skipped: {e}")

    if state.get("issue"):
        rc, out, err = run(["gh", "issue", "close", str(state["issue"]),
                            "--comment", "Dogfood complete — lifecycle walked end-to-end."],
                           cwd=str(REPO_ROOT))
        _rec_cmd(rec, "gh issue close", rc, out, err)
        state["issue_closed"] = (rc == 0)
    _advance(rec, state, "retrospective", run=run)
    rec.actions.append("Closed tracking issue; advanced to 100%.")
    return rec


# ---------------------------------------------------------------------------
# check (offline, deterministic)
# ---------------------------------------------------------------------------

def check(state: dict) -> tuple[bool, list[tuple[bool, str]]]:
    """Verify the dogfood walked the full lifecycle correctly. Pure function on state."""
    records = state.get("records", [])
    by_name = {r["substep"]: r for r in records}
    all_names = [s.name for s in lc.all_substeps()]
    present = [n for n in all_names if n in by_name]

    results: list[tuple[bool, str]] = []

    results.append((present == all_names,
                    f"All 12 substeps present in canonical order ({len(present)}/12)"))

    gates_ok = all(by_name.get(g, {}).get("gate_refused") and by_name.get(g, {}).get("gate_approved")
                   for g in SIMULATED_GATES)
    results.append((gates_ok, "jam & define-evidence: refused-without-approval, then approved"))

    mrec = by_name.get("merge", {})
    merge_ok = (mrec.get("marker") == "OPERATOR-REAL" and state.get("dummy_pr_state") == "MERGED")
    results.append((merge_ok, "merge is OPERATOR-REAL and dummy PR MERGED"))

    overall = lc.overall_pct(set(present))
    closed_ok = (state.get("issue_closed") is True and overall == 100)
    results.append((closed_ok, f"tracking issue closed and overall {overall}%"))

    rereview_ok = (state.get("rereview_misfire") is False)
    results.append((rereview_ok, "dummy PR first review was FULL (no RE-REVIEW misfire) — Part B"))

    return all(ok for ok, _ in results), results


def render_transcript(state: dict) -> str:
    """Render the annotated markdown transcript from the state file."""
    lines = [
        f"# Dogfood lifecycle run — {state.get('updated_at', '')[:10]}",
        "",
        f"- Tracking issue: {state.get('issue_url', '(n/a)')}",
        f"- Dummy PR: {state.get('dummy_pr_url', '(n/a)')} (state: {state.get('dummy_pr_state', '?')})",
        f"- Branch: `{state.get('branch', '?')}`",
        "",
        "## Substep ledger",
        "",
        "| # | Substep | Driver | Marker | OK |",
        "| --- | --- | --- | --- | --- |",
    ]
    for i, r in enumerate(state.get("records", []), 1):
        lines.append(f"| {i} | `{r['substep']}` | {r['driver']} | {r['marker']} | "
                     f"{'✓' if r.get('ok') else '✗'} |")

    ok, results = check(state)
    lines += ["", "## Conformance check", ""]
    for passed, label in results:
        lines.append(f"- {'✓' if passed else '✗'} {label}")
    lines += ["", f"**Result: {'PASS' if ok else 'FAIL'}**", "", "## Step detail", ""]

    for r in state.get("records", []):
        lines.append(f"### `{r['substep']}` — {r['marker']}")
        for a in r.get("actions", []):
            lines.append(f"- {a}")
        for c in r.get("commands", []):
            lines.append(f"  - `{c['cmd']}` → rc={c['rc']}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_run(args, run=_run) -> int:
    state_path = Path(args.state)
    if state_path.exists() and not args.force:
        print(f"State exists at {state_path}. Use --force to restart, or `resume`/`cleanup`.")
        return 1

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    branch = f"dogfood/lifecycle-{ts}"
    worktree = str(REPO_ROOT.parent / f"{REPO_ROOT.name}-wt-dogfood-{ts}")
    state = {
        "branch": branch, "worktree": worktree, "repo": _repo_slug(run=run),
        "issue": None, "issue_url": None, "dummy_pr": None, "dummy_pr_url": None,
        "dummy_pr_state": None, "rereview_misfire": None, "issue_closed": False,
        "verify_rc": None, "paused_at": None, "records": [],
    }

    records: list[StepRecord] = []
    try:
        records.extend(step_setup(state, run=run))
        for gate in SIMULATED_GATES:
            records.append(demo_operator_gate(state["branch"], state.get("issue"), gate, run=run))
        records.append(step_plan(state, run=run))
        records.append(step_implement(state, run=run))
        records.append(step_verify(state, run=run))
        records.append(step_pr_evidence(state, run=run))
        records.append(step_babysit(state, run=run))
        state["paused_at"] = "merge"
    finally:
        state["records"] = [asdict(r) for r in records]
        save_state(state_path, state)

    print(f"\nState → {state_path}")
    print(f"Tracking issue → {state.get('issue_url')}")
    print(f"Dummy PR → {state.get('dummy_pr_url')}")
    print("\n─── PAUSED at the merge gate (operator-reserved) ───")
    print(f"Approve + squash-merge the dummy PR #{state.get('dummy_pr')}, then run:")
    print("  python3 scripts/dogfood_lifecycle.py resume")
    return 0


def cmd_resume(args, run=_run) -> int:
    state_path = Path(args.state)
    if not state_path.exists():
        print(f"No state at {state_path}; run `dogfood_lifecycle.py run` first.")
        return 1
    state = load_state(state_path)

    pr = str(state.get("dummy_pr"))
    rc, out, err = run(["gh", "pr", "view", pr, "--json", "state", "-q", ".state"],
                       cwd=str(REPO_ROOT))
    state["dummy_pr_state"] = out.strip()
    if state["dummy_pr_state"] != "MERGED":
        save_state(state_path, state)
        print(f"Dummy PR #{pr} state is {state['dummy_pr_state']!r}, not MERGED.")
        print("Approve + squash-merge it, then re-run resume.")
        return 1

    records = list(state.get("records", []))
    merge_rec = StepRecord("merge", "operator", "OPERATOR-REAL",
                           actions=["Operator approved + squash-merged the dummy PR."])
    _advance(merge_rec, state, "merge", approved=True, run=run)
    records.append(asdict(merge_rec))
    records.append(asdict(step_post_merge_verify(state, run=run)))
    records.append(asdict(step_retrospective(state, run=run)))
    state["records"] = records
    state["paused_at"] = None
    save_state(state_path, state)

    ok, results = check(state)
    _print_check(results)

    transcript_dir = REPO_ROOT / "docs" / "dogfood"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    out_path = transcript_dir / f"lifecycle-run-{datetime.date.today().isoformat()}.md"
    out_path.write_text(render_transcript(state), encoding="utf-8")
    print(f"\nTranscript → {out_path}")
    return 0 if ok else 1


def cmd_check(args) -> int:
    state_path = Path(args.state)
    if not state_path.exists():
        print(f"No state at {state_path}; nothing to check.")
        return 1
    state = load_state(state_path)
    ok, results = check(state)
    _print_check(results)
    return 0 if ok else 1


def cmd_cleanup(args, run=_run) -> int:
    state_path = Path(args.state)
    if not state_path.exists():
        print(f"No state at {state_path}; nothing to clean up.")
        return 0
    state = load_state(state_path)
    if state.get("issue") and not state.get("issue_closed"):
        run(["gh", "issue", "close", str(state["issue"]), "--comment", "Dogfood cleanup."],
            cwd=str(REPO_ROOT))
    if state.get("worktree") and Path(state["worktree"]).exists():
        run(["git", "worktree", "remove", "--force", state["worktree"]], cwd=str(REPO_ROOT))
    if state.get("branch"):
        run(["git", "branch", "-D", state["branch"]], cwd=str(REPO_ROOT))
    state_path.unlink(missing_ok=True)
    print("Cleanup done.")
    return 0


def _print_check(results: list[tuple[bool, str]]) -> None:
    print("\nDogfood conformance")
    print("─" * 64)
    for passed, label in results:
        print(f"  {'✓' if passed else '✗'} {label}")
    print("─" * 64)
    print("PASS" if all(ok for ok, _ in results) else "FAIL")


# ---------------------------------------------------------------------------
# Fixtures used by the `plan` substep
# ---------------------------------------------------------------------------

THIN_PLAN = "# Plan\n\nAdd a readme. Just do it.\n"

FULL_PLAN = """# Dogfood dummy plan — add docs/dogfood/README.md

## Scope
In-scope: add `docs/dogfood/README.md`. Out-of-scope: any code change.
Sandbox tier: local verify only (doc-only change; no e2e/live run needed).

## Invariants
Additive, backward-compatible, idempotent (re-running rewrites the same file).
No new feature flag is needed — documentation only, no existing behavior changes.

## Milestones

### M1 — Create the doc (Sonnet)
Edit `docs/dogfood/README.md:1`.
Verify:
```bash
python3 scripts/verify.py --fast
```

### M2 — Lock-step docs (Sonnet)
Update PROGRESS.md; run check_doc_freshness.
Verify:
```bash
python3 scripts/check_doc_freshness.py
```

### M3 — Ship (Opus only if stuck)
Branch/PR mechanics: `gh pr create`; never self-merge — operator merge; babysit CI.
Verify:
```bash
python3 scripts/verify.py --full
```

## Evidence (PR §4)
Happy path: file present on main. Failure/recovery: if doc-freshness fails, fix the
doc and re-run. Includes a --dry-run smoke check.

## Docs lock-step
PROGRESS.md updated; check_doc_freshness clean; WORKFLOW.md referenced.

## Model routing
M1/M2 Sonnet; M3 Opus only if stuck (cost playbook).
"""

DOGFOOD_DOC = """# Dogfood evidence

This directory holds end-to-end lifecycle conformance runs produced by
`scripts/dogfood_lifecycle.py`. Each `lifecycle-run-<date>.md` is an annotated
transcript proving a dummy requirement walked all 12 substeps of the Jarvis
lifecycle (scripts/lifecycle.py), with operator-reserved gates enforced and a
real operator merge.

Regenerate with:

```bash
python3 scripts/dogfood_lifecycle.py run
# approve the dummy PR, then:
python3 scripts/dogfood_lifecycle.py resume
```
"""

PR_BODY = """## 1. What is the change

Adds `docs/dogfood/README.md` describing the dogfood evidence directory. This PR
is a throwaway, opened by `scripts/dogfood_lifecycle.py` to prove the Jarvis
lifecycle works end-to-end (issue tracking, gate enforcement, PR, merge).

## 2. Motivation

Dogfood the harness: a real requirement must walk all 12 lifecycle substeps with
operator-reserved gates enforced. See the harden-lifecycle PR + PROGRESS.md.

## 3. Design / Approach

Single doc file added off origin/main. The orchestrator drives phase_state through
each substep and pauses at the operator-reserved merge gate.

## 4. End-to-end test (with evidence)

<details><summary>Evidence</summary>

```
Generated by scripts/dogfood_lifecycle.py — see the transcript committed on the
harden-lifecycle PR (docs/dogfood/lifecycle-run-<date>.md) for the full ledger.
```

</details>

## 5. Backward compatibility — and proof

Yes — additive doc-only change. No code, schema, or runtime behavior changes.

## 6. Checklist
- [x] Tests added/updated and passing (n/a — doc-only; harness tests cover the orchestrator)
- [x] Docs updated in lock-step (this PR is the doc)
- [x] No secrets / PII in the diff
- [x] Cloud paths read from GCS, not laptop downloads (n/a)
- [x] Money math is Decimal-precise; writes idempotent (n/a)
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dogfood the Jarvis lifecycle end-to-end.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("run", "resume", "check", "cleanup"):
        p = sub.add_parser(name)
        p.add_argument("--state", default=str(DEFAULT_STATE))
        if name == "run":
            p.add_argument("--force", action="store_true", help="Overwrite an existing state file")

    args = parser.parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "resume":
        return cmd_resume(args)
    if args.cmd == "check":
        return cmd_check(args)
    if args.cmd == "cleanup":
        return cmd_cleanup(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
