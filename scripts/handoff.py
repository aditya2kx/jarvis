#!/usr/bin/env python3
"""Hand a PR's work between the Mac and a Cursor Cloud Agent (either direction).

    # Leaving surface (its agent runs this once the work is committed):
    python3 scripts/handoff.py out --pr 388 --to cloud --note-file note.md
    python3 scripts/handoff.py out --pr 388 --to mac   --note-file note.md

    # Arriving surface (first thing its agent runs):
    python3 scripts/handoff.py in --pr 388

`out` refuses uncommitted or unpushed work, posts the note as a PR comment (the arriving agent's
context — chat history does not travel), and marks the phase cache handed off so the Mac listener
stops routing this branch's signals. `--to cloud` also comments `/jarvis-new-task` on the PR,
which starts a Cloud Agent on the PR's own branch (open it from Cursor mobile/web/desktop).

`in` checks the branch out (Mac: its own worktree, then a Cursor window), restores phase tracking
from the tracking issue (GitHub is the source of truth) and prints the latest handoff note.
See docs/contributing/dev-anywhere.md § Handoff.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import phase_state as P  # noqa: E402
from gcp_access_probe import detect_surface  # noqa: E402

MARKER = "<!-- jarvis-handoff"
_CLOSES_RE = re.compile(r"\b(?:closes|fixes|resolves)\s+#(\d+)", re.IGNORECASE)
_DONE_RE = re.compile(r"^Done:\s*(.+)$", re.MULTILINE)
SURFACES = {"mac_local": "mac", "cursor_cloud": "cloud"}


class HandoffError(RuntimeError):
    pass


def _run(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise HandoffError(f"{' '.join(args[:3])} failed: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.strip()


def _gh_json(*args: str) -> dict | list:
    return json.loads(_run("gh", *args))


def surface() -> str:
    return SURFACES.get(detect_surface(), "other")


def tracking_issue(pr_body: str) -> int | None:
    m = _CLOSES_RE.search(pr_body or "")
    return int(m.group(1)) if m else None


def done_from_issue(issue_body: str) -> list[str]:
    """Done substeps from the issue's <!-- phase-state --> block (`Done: a, b` / `Done: none`)."""
    start = (issue_body or "").find(P._STATUS_START)
    if start < 0:
        return []
    m = _DONE_RE.search(issue_body[start:])
    if not m or m.group(1).strip() == "none":
        return []
    return [s.strip() for s in m.group(1).split(",") if s.strip()]


def latest_note(comments: list[dict]) -> str | None:
    notes = [c.get("body", "") for c in comments if MARKER in (c.get("body") or "")]
    return notes[-1] if notes else None


def note_comment(note: str, *, to: str, frm: str, sha: str, branch: str, done: list[str]) -> str:
    nxt = ("A Cloud Agent starts on this branch from the `/jarvis-new-task` comment below."
           if to == "cloud" else
           f"On the Mac: `python3 scripts/handoff.py in --pr <this PR>` (worktree on `{branch}` + Cursor window).")
    return (
        f"{MARKER} to={to} from={frm} sha={sha} -->\n"
        f"**Handoff {frm} → {to}** at `{sha[:7]}` on `{branch}`. "
        f"Phase done: {', '.join(done) or 'none'}.\n\n{note.strip()}\n\n{nxt}"
    )


def _cache_paths(branch: str, root: Path) -> list[Path]:
    """`root`'s phase cache, plus the main checkout's (where the Mac listener reads first)."""
    name = f"session-{P._slug(branch)}-phase.json"
    paths = [root / "metrics" / "pr_cost" / name]
    common = _run("git", "rev-parse", "--path-format=absolute", "--git-common-dir", cwd=root, check=False)
    if common:
        main = Path(common).parent / "metrics" / "pr_cost" / name
        if main.resolve() != paths[0].resolve():
            paths.append(main)
    return paths


def _update_caches(branch: str, root: Path, **fields) -> dict:
    """Merge `fields` into every copy of the branch's phase cache; returns the first copy."""
    merged = []
    for path in _cache_paths(branch, root):
        data = json.loads(path.read_text()) if path.exists() else P._load_cache(branch)
        data.update(fields)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))
        merged.append(data)
    return merged[0]


def cmd_out(pr: int, to: str, note: str) -> str:
    frm = surface()
    if frm == to:
        raise HandoffError(f"already on {to}")
    if _run("git", "status", "--porcelain"):
        raise HandoffError("uncommitted changes — commit them (a WIP commit is fine), then retry")
    branch = _run("git", "rev-parse", "--abbrev-ref", "HEAD")
    _run("git", "fetch", "origin", branch)
    if _run("git", "rev-list", f"origin/{branch}..HEAD"):
        raise HandoffError(f"unpushed commits — `git push -u origin {branch}`, then retry")
    meta = _gh_json("pr", "view", str(pr), "--json", "headRefName")
    if meta["headRefName"] != branch:
        raise HandoffError(f"PR #{pr} is on {meta['headRefName']!r}, this checkout is on {branch!r}")
    if frm == "mac":
        # The Mac session's cost only lands through capture-build; the Cloud Agent's is in its marker.
        _run("python3", "scripts/pr_cost_ledger.py", "capture-build", "--pr", str(pr), check=False)
    sha = _run("git", "rev-parse", "HEAD")
    data = _update_caches(branch, Path(_run("git", "rev-parse", "--show-toplevel")), handed_off_to=to, pr=pr)
    _run("gh", "pr", "comment", str(pr), "--body",
         note_comment(note, to=to, frm=frm, sha=sha, branch=branch, done=data.get("done", [])))
    if to == "cloud":
        _run("gh", "pr", "comment", str(pr), "--body",
             f"/jarvis-new-task continue PR #{pr} from the handoff note above")
    return f"handoff: PR #{pr} ({branch} @ {sha[:7]}) → {to}"


def _checkout(branch: str) -> Path:
    _run("git", "fetch", "origin", branch)
    if surface() != "mac":
        if _run("git", "rev-parse", "--abbrev-ref", "HEAD") != branch:
            _run("git", "checkout", "-B", branch, f"origin/{branch}")
        _run("git", "pull", "--ff-only", "origin", branch)
        return Path(_run("git", "rev-parse", "--show-toplevel"))
    import new_requirement as NR

    for block in _run("git", "worktree", "list", "--porcelain").split("\n\n"):
        if f"branch refs/heads/{branch}" in block:
            wt = Path(block.splitlines()[0].removeprefix("worktree "))
            _run("git", "pull", "--ff-only", "origin", branch, cwd=wt)
            return wt
    wt = NR.default_worktree_path(NR._repo_root(), branch)
    if NR._branch_exists(NR._repo_root(), branch):
        _run("git", "worktree", "add", str(wt), branch)
        _run("git", "pull", "--ff-only", "origin", branch, cwd=wt)
    else:
        _run("git", "worktree", "add", "-b", branch, str(wt), f"origin/{branch}")
    return wt


def cmd_in(pr: int, open_cursor: bool = True) -> str:
    meta = _gh_json("pr", "view", str(pr), "--json", "headRefName,body,comments,url")
    branch = meta["headRefName"]
    wt = _checkout(branch)
    issue = tracking_issue(meta["body"])
    done = done_from_issue(_gh_json("issue", "view", str(issue), "--json", "body")["body"]) if issue else []
    fields = {"issue": issue, "pr": pr, "handed_off_to": None, "worktree_path": str(wt)}
    if done:  # an issue without a phase-state block must not wipe a cache that has one
        fields["done"] = done
    done = _update_caches(branch, wt, **fields).get("done", [])
    note = latest_note(meta.get("comments") or []) or "(no handoff note — read the PR body and its review threads)"
    if surface() == "mac" and open_cursor:
        import start_pr_session as S

        cursor = S.find_cursor_cli()
        if cursor:
            subprocess.Popen([str(cursor), "-n", str(wt)])
            prompt = (f"Continue PR #{pr} ({meta['url']}). Run `python3 scripts/handoff.py in --pr {pr} "
                      "--no-open` and pick up from the handoff note it prints.")
            subprocess.run(["open", S.make_deeplink(prompt, mode="agent")], check=False)
    return (f"handoff in: PR #{pr} on {branch} at {wt}; tracking issue #{issue or 'none'}, "
            f"phase done: {', '.join(done) or 'none'}\n\n{note}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("out", help="leave: post the handoff note, stop local routing")
    o.add_argument("--pr", type=int, required=True)
    o.add_argument("--to", choices=("cloud", "mac"), required=True)
    o.add_argument("--note-file", required=True, help="what's done, what's next, open threads")
    i = sub.add_parser("in", help="arrive: check out the branch, restore phase tracking, print the note")
    i.add_argument("--pr", type=int, required=True)
    i.add_argument("--no-open", action="store_true", help="Mac: skip opening a Cursor window")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "out":
            print(cmd_out(a.pr, a.to, Path(a.note_file).read_text()))
        else:
            print(cmd_in(a.pr, open_cursor=not a.no_open))
    except HandoffError as e:
        print(f"handoff: {a.cmd} refused — {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
