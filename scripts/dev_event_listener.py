#!/usr/bin/env python3
"""Local event listener — catch up and route jarvis-signal comments to worktree inboxes.

CLI subcommands:
    catch-up --issue <N> [--branch <b>] [--since <ISO>] [--dry-run]
        Read jarvis-signal comments on a GitHub tracking issue since the last
        cursor (or since the given ISO timestamp). Route each signal to the owning
        worktree inbox via dev_event_router. Updates last_signal_cursor in the phase
        cache.  Returns the count of delivered signals.

    watch --issue <N> [--branch <b>] [--interval <sec>] [--dry-run]
        Poll for new signals on the given interval (default 60 s). Runs until
        interrupted (Ctrl-C). Calls catch-up on each tick.

    watch-all [--interval <sec>] [--dry-run]
        Always-on daemon mode. Each tick enumerates every open jarvis-work issue
        and every open PR, then calls catch-up on each. Intended to be run via
        launchd (auto-start on login, auto-restart on crash). Handles intake
        (creates new worktrees) as well as all ci/comment/merge routing.

    dispatch --branch <b> --event-json '<json>'
        Open/focus the worktree window (OQ-8) and — if AUTO_DISPATCH is on and
        the chat is idle — seed the drain prompt. Non-preemptive: if the chat is
        busy the event stays in the inbox and the operator is notified.

    ensure-daemon [--interval <sec>]
        Idempotently install and load the launchd LaunchAgent that runs watch-all.
        Safe to call on every new_requirement.py run — no-op if already running.

Design:
    - stdlib-only for catch-up/watch; dispatch uses subprocess for cursor CLI +
      osascript (macOS only).
    - gh CLI is the only network dependency for catch-up/watch.
    - All routing goes through dev_event_router.route_signal (no direct inbox writes).
    - Intake signals are handled in catch_up before route_signal (intake has no
      branch, so route_signal returns unrouted; the listener is the intake gate).
    - Busy detection via session-<slug>-status.json written by Cursor hooks.
    - AUTO_DISPATCH controlled by LOCAL_EVENT_AUTO_DISPATCH env var (default on).
    - AUTO_OPEN controlled by LOCAL_EVENT_AUTO_OPEN env var (default on).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
METRICS_DIR = REPO_ROOT / "metrics" / "pr_cost"

STALE_LOCK_SEC = 600   # heartbeat older than 10 min → treat as idle

# env-var defaults (both default ON per plan OQ-9)
_AUTO_OPEN_DEFAULT = "1"
_AUTO_DISPATCH_DEFAULT = "1"

# Seen-file for intake dedup — prevents re-running new_requirement.py on rescan
_INTAKE_SEEN_FILE = METRICS_DIR / "listener-intake-seen.json"

# launchd agent label / plist path
_LAUNCHD_LABEL = "com.jarvis.devsignals"
_LAUNCHD_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCHD_LABEL}.plist"

# ---------------------------------------------------------------------------
# Import router helpers
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).parent))
from post_merge_lifecycle import parse_signal, find_tracking_issue_from_cache
import dev_event_router as _R


# ---------------------------------------------------------------------------
# Phase-cache helpers
# ---------------------------------------------------------------------------

def _slug(branch: str) -> str:
    import re
    return re.sub(r"[^a-zA-Z0-9_-]", "-", branch)[:60]


def _phase_path(branch: str) -> Path:
    return METRICS_DIR / f"session-{_slug(branch)}-phase.json"


def _status_path(branch: str) -> Path:
    return METRICS_DIR / f"session-{_slug(branch)}-status.json"


def _load_phase(branch: str) -> dict | None:
    p = _phase_path(branch)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _save_phase(branch: str, data: dict) -> None:
    import datetime
    data["updated_at"] = datetime.datetime.utcnow().isoformat() + "Z"
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    _phase_path(branch).write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# GH API helpers
# ---------------------------------------------------------------------------

def _gh_issue_comments(issue: int) -> list[dict]:
    """Fetch all comments for a GH issue as a list of dicts (body + created_at)."""
    try:
        out = subprocess.check_output(
            ["gh", "issue", "view", str(issue),
             "--json", "comments", "-q", ".comments"],
            text=True, stderr=subprocess.DEVNULL, timeout=30,
        )
        return json.loads(out or "[]")
    except Exception:
        return []


def _gh_open_jarvis_issue_numbers() -> list[int]:
    """Return issue numbers for all open jarvis-work issues.

    Two sources are merged to ensure freshly-created issues (which have the
    jarvis-work label added by the intake-signal job but may not yet be visible
    in the label-filtered list on the first poll) are still caught:

    1. Issues explicitly labelled ``jarvis-work`` (the stable set).
    2. The most-recently-updated open issues (last 20), which covers the window
       between /jarvis-new-task comment and the label being added by the workflow.
    """
    numbers: list[int] = []

    # Source 1: labelled jarvis-work
    try:
        out = subprocess.check_output(
            ["gh", "issue", "list", "--label", "jarvis-work",
             "--state", "open", "--limit", "50",
             "--json", "number", "-q", "[.[].number]"],
            text=True, stderr=subprocess.DEVNULL, timeout=30,
        )
        numbers.extend(json.loads(out or "[]"))
    except Exception:
        pass

    # Source 2: most recently updated open issues (covers pre-label window)
    try:
        out = subprocess.check_output(
            ["gh", "issue", "list", "--state", "open", "--limit", "20",
             "--json", "number,updatedAt",
             "-q", "[.[] | select(.updatedAt > (now - 300 | todate)) | .number]"],
            text=True, stderr=subprocess.DEVNULL, timeout=30,
        )
        numbers.extend(json.loads(out or "[]"))
    except Exception:
        pass

    # Deduplicate while preserving order for readability in logs
    seen: set[int] = set()
    result: list[int] = []
    for n in numbers:
        if n not in seen:
            seen.add(n)
            result.append(n)
    return result


def _gh_open_pr_numbers() -> list[int]:
    """Return PR numbers for all open PRs in the repo."""
    try:
        out = subprocess.check_output(
            ["gh", "pr", "list", "--state", "open", "--limit", "50",
             "--json", "number", "-q", "[.[].number]"],
            text=True, stderr=subprocess.DEVNULL, timeout=30,
        )
        return json.loads(out or "[]")
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Intake seen-file (prevents re-creating a worktree on rescan)
# ---------------------------------------------------------------------------

def _load_intake_seen() -> set[str]:
    try:
        if _INTAKE_SEEN_FILE.exists():
            return set(json.loads(_INTAKE_SEEN_FILE.read_text()))
    except Exception:
        pass
    return set()


def _save_intake_seen(seen: set[str]) -> None:
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    _INTAKE_SEEN_FILE.write_text(json.dumps(sorted(seen)))


# ---------------------------------------------------------------------------
# Catch-up
# ---------------------------------------------------------------------------

def catch_up(
    issue: int,
    *,
    branch: str | None = None,
    since: str | None = None,
    dry_run: bool = False,
) -> int:
    """Deliver un-seen jarvis-signal comments on ``issue`` to the owning inbox.

    Reads all comments on the tracking issue, extracts jarvis-signal blocks,
    filters by ``since`` (ISO timestamp), routes each via dev_event_router.

    ``branch`` is optional — when given, it restricts routing to signals whose
    branch field matches (useful when one issue tracks multiple branches).
    ``since`` defaults to the ``last_signal_cursor`` in the phase cache for
    the matched branch (or epoch 0 if no cache). The cursor is not written back
    here; dedup relies on the router's ``delivered_signals`` set (idempotent).

    Intake signals (event == "intake") are handled before route_signal — they
    need no branch, go through an author allowlist + seen-file dedup, then call
    _dispatch("", signal) which invokes new_requirement.py.

    Returns the count of newly delivered signals.
    """
    comments = _gh_issue_comments(issue)
    if not comments:
        return 0

    # Determine since timestamp
    cursor: str | None = since
    if cursor is None and branch:
        cache = _load_phase(branch)
        cursor = cache.get("last_signal_cursor") if cache else None

    delivered = 0
    for comment in comments:
        created = comment.get("createdAt") or comment.get("created_at") or ""
        if cursor and created and created <= cursor:
            continue  # already seen
        body = comment.get("body") or ""
        signal = parse_signal(body)
        if signal is None:
            continue
        sig_branch = signal.get("branch") or ""
        if branch and sig_branch and sig_branch != branch:
            continue  # different branch

        if dry_run:
            print(f"(dry-run) would route signal: {signal.get('event')} on {sig_branch or issue}")
            delivered += 1
            continue

        author = comment.get("author", {}).get("login") if isinstance(comment.get("author"), dict) else None

        # Intake signals have no branch — route_signal returns unrouted for them.
        # Handle them here: allowlist check + seen-file dedup + run new_requirement.py.
        if signal.get("event") == "intake":
            if author not in _R.ALLOWED_AUTHORS:
                print(f"signal {signal.get('id', '?')[:8]} → unauthorized  [intake author={author}]")
                continue
            sid = signal.get("id") or ""
            seen = _load_intake_seen()
            if sid and sid in seen:
                print(f"signal {sid[:8]} → duplicate  [intake already dispatched]")
                continue
            requirement = signal.get("requirement") or ""
            print(f"signal {sid[:8]} → intake  [requirement={requirement[:60]!r}]")
            _dispatch("", signal)
            if sid:
                seen.add(sid)
                _save_intake_seen(seen)
            delivered += 1
            continue

        verdict = _R.route_signal(signal, author=author)
        print(f"signal {signal.get('id', '?')[:8]} → {verdict}  [{signal.get('event')} branch={sig_branch or '?'}]")
        if verdict == "delivered":
            delivered += 1
            # Dispatch: open/focus worktree + (if idle) seed drain prompt
            _dispatch(sig_branch or "", signal)

    return delivered


# ---------------------------------------------------------------------------
# Watch (poll loop)
# ---------------------------------------------------------------------------

def watch(
    issue: int,
    *,
    branch: str | None = None,
    interval: int = 60,
    dry_run: bool = False,
) -> None:
    """Poll for new signals on ``issue`` every ``interval`` seconds."""
    print(f"Watching issue #{issue} every {interval}s … (Ctrl-C to stop)")
    while True:
        try:
            n = catch_up(issue, branch=branch, dry_run=dry_run)
            if n:
                print(f"  delivered {n} signal(s)")
        except Exception as exc:
            print(f"  watch tick error (non-fatal): {exc}", file=sys.stderr)
        time.sleep(interval)


def watch_all(
    *,
    interval: int = 30,
    dry_run: bool = False,
) -> None:
    """Always-on daemon: each tick catch-up every open jarvis-work issue + open PR.

    Handles intake (creates new worktrees via new_requirement.py) and all
    ci/comment/merge routing. Intended to be managed by launchd — auto-starts
    on login, auto-restarts on crash. No per-PR watcher process is needed.
    """
    print(f"watch-all: polling every {interval}s (Ctrl-C to stop)", flush=True)
    while True:
        try:
            issues = _gh_open_jarvis_issue_numbers()
            prs = _gh_open_pr_numbers()
            targets = sorted(set(issues + prs))
            total = 0
            for n in targets:
                try:
                    delivered = catch_up(n, dry_run=dry_run)
                    total += delivered
                except Exception as exc:
                    print(f"  catch-up #{n} error (non-fatal): {exc}", file=sys.stderr, flush=True)
            if total:
                print(f"  watch-all: delivered {total} signal(s) across {len(targets)} targets", flush=True)
        except Exception as exc:
            print(f"  watch-all tick error (non-fatal): {exc}", file=sys.stderr, flush=True)
        time.sleep(interval)


# ---------------------------------------------------------------------------
# Auto-open / focus worktree window (OQ-8)
# ---------------------------------------------------------------------------

def _auto_open_enabled() -> bool:
    return os.environ.get("LOCAL_EVENT_AUTO_OPEN", _AUTO_OPEN_DEFAULT) not in ("0", "false", "no")


def _auto_dispatch_enabled() -> bool:
    return os.environ.get("LOCAL_EVENT_AUTO_DISPATCH", _AUTO_DISPATCH_DEFAULT) not in ("0", "false", "no")


def _cursor_open(path: Path) -> None:
    """Open or focus the Cursor IDE on ``path``.

    Prefers the ``cursor`` CLI (opens-or-focuses the folder window).
    Falls back to ``open -a Cursor`` so this never hits a "can't do this".
    """
    cursor_bin = shutil.which("cursor")
    if cursor_bin:
        subprocess.run([cursor_bin, str(path)], check=False)
    else:
        subprocess.run(["open", "-a", "Cursor", str(path)], check=False)
    # Raise Cursor to the foreground (macOS) — raise to front covers minimized case
    subprocess.run(
        ["osascript", "-e", 'tell application "Cursor" to activate'],
        check=False, capture_output=True,
    )


def _open_or_focus_worktree(
    path: Path,
    *,
    requirement: str | None = None,
    create_if_missing: bool = False,
) -> None:
    """Open or focus the Cursor window for ``path``."""
    if not path.exists():
        if create_if_missing and requirement:
            subprocess.run(
                [sys.executable, str(REPO_ROOT / "scripts" / "new_requirement.py"),
                 "--requirement", requirement],
                check=False,
            )
        return
    _cursor_open(path)


# ---------------------------------------------------------------------------
# Busy detection (reads status lock written by Cursor hooks)
# ---------------------------------------------------------------------------

def _worktree_busy(branch: str) -> bool:
    """Return True when the worktree's Cursor chat is currently running a turn."""
    status_file = _status_path(branch)
    if not status_file.exists():
        return False
    try:
        data = json.loads(status_file.read_text())
    except Exception:
        return False
    if data.get("state") != "busy":
        return False
    # Stale lock check
    heartbeat_str = data.get("heartbeat")
    if not heartbeat_str:
        return False
    import datetime
    try:
        hb = datetime.datetime.fromisoformat(heartbeat_str.rstrip("Z")).replace(
            tzinfo=datetime.timezone.utc
        )
        elapsed = time.time() - hb.timestamp()
        if elapsed > STALE_LOCK_SEC:
            return False  # stale — treat as idle
    except Exception:
        return False
    return True


def _pending_count(branch: str) -> int:
    cache = _load_phase(branch)
    return (cache or {}).get("pending_event_count", 0)


def _worktree_path_for(branch: str) -> Path | None:
    """Resolve the worktree path from the phase cache, or derive the default."""
    cache = _load_phase(branch)
    if cache and cache.get("worktree_path"):
        return Path(cache["worktree_path"])
    if not branch:
        return None
    # Derive using new_requirement.default_worktree_path convention
    # ../jarvis-wt-<slug>
    slug = _slug(branch)
    repo_name = REPO_ROOT.name.split("-wt-")[0] if "-wt-" in REPO_ROOT.name else REPO_ROOT.name
    default = REPO_ROOT.parent / f"{repo_name}-wt-{slug}"
    if default.exists():
        return default
    return None


def _seed_drain_prompt(branch: str, wt: Path) -> None:
    """Fire the cold-start deeplink to pre-seed the drain prompt in a new chat."""
    try:
        import sys as _sys
        _sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import start_pr_session as S
        import pr_cost_ledger as L
        brief_rel = f"metrics/pr_cost/session-{L._slug(branch)}-brief.md"
        seed = f"[{branch}] You have pending events in your inbox. Drain the FIFO queue:\n\n" \
               f"```bash\npython3 scripts/dev_event_router.py drain --branch {branch}\n```\n\n" \
               f"Read the event kind and act on it (babysit for babysit_ci, retrospective for retrospective)."
        deeplink = S.make_deeplink(seed, mode="agent")
        S.open_cursor_handoff(folder=wt, deeplink=deeplink,
                              launch_html=wt / "metrics" / "pr_cost" / f"session-{L._slug(branch)}-launch.html",
                              delay_sec=1.5)
    except Exception as exc:
        print(f"  (cold-start seed failed, non-fatal): {exc}", file=sys.stderr)


def _notify(branch: str, message: str) -> None:
    """Send a macOS notification (best-effort)."""
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{message}" with title "Jarvis dev signals"'],
            check=False, capture_output=True, timeout=5,
        )
    except Exception:
        pass


def _dispatch(branch: str, event: dict) -> str:
    """Open/focus the worktree window and optionally start the drain agent.

    Returns one of: dispatched | queued | notify_only | no_worktree | disabled
    """
    if not _auto_open_enabled():
        return "disabled"

    event_kind = event.get("event", "unknown")
    requirement = event.get("requirement")

    wt = _worktree_path_for(branch)
    is_intake = event_kind == "intake"
    _open_or_focus_worktree(
        wt or Path("/nonexistent"),
        requirement=requirement,
        create_if_missing=is_intake,
    )

    if wt is None or not wt.exists():
        return "no_worktree"

    if not _auto_dispatch_enabled():
        _notify(branch, f"Event {event_kind} queued — AUTO_DISPATCH is off.")
        return "notify_only"

    if _worktree_busy(branch):
        count = _pending_count(branch)
        _notify(branch, f"{count} event(s) queued — chat busy, will drain when idle.")
        return "queued"

    # Idle — seed the drain prompt (one-click cold start)
    _seed_drain_prompt(branch, wt)
    return "dispatched"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def ensure_daemon(interval: int = 30) -> str:
    """Idempotently install and load the launchd LaunchAgent that runs watch-all.

    Writes ~/Library/LaunchAgents/com.jarvis.devsignals.plist and loads it
    with launchctl. Safe to call repeatedly — no-op if the agent is already
    loaded. Returns 'installed' | 'already_running' | 'load_failed' | 'not_macos'.
    """
    if sys.platform != "darwin":
        print("ensure-daemon: not macOS — skipping launchd install", file=sys.stderr)
        return "not_macos"

    python_bin = sys.executable
    listener_script = str(Path(__file__).resolve())
    log_dir = REPO_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_out = str(log_dir / "dev-daemon.log")
    log_err = str(log_dir / "dev-daemon-err.log")

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{_LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python_bin}</string>
    <string>-u</string>
    <string>{listener_script}</string>
    <string>watch-all</string>
    <string>--interval</string>
    <string>{interval}</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>
    <string>{Path.home()}</string>
    <key>LOCAL_EVENT_AUTO_OPEN</key>
    <string>1</string>
    <key>LOCAL_EVENT_AUTO_DISPATCH</key>
    <string>0</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
    <key>PATH</key>
    <string>{Path.home() / ".local" / "bin"}:/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:{Path(python_bin).parent}</string>
    <key>PYTHONPATH</key>
    <string>{str(REPO_ROOT / "scripts")}</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{log_out}</string>
  <key>StandardErrorPath</key>
  <string>{log_err}</string>
  <key>WorkingDirectory</key>
  <string>{str(REPO_ROOT)}</string>
  <key>ThrottleInterval</key>
  <integer>10</integer>
</dict>
</plist>
"""

    # Check if already running
    try:
        result = subprocess.run(
            ["launchctl", "list", _LAUNCHD_LABEL],
            capture_output=True, timeout=5,
        )
        if result.returncode == 0:
            print(f"ensure-daemon: {_LAUNCHD_LABEL} already loaded.")
            return "already_running"
    except Exception:
        pass

    # Write plist
    _LAUNCHD_PLIST.parent.mkdir(parents=True, exist_ok=True)
    _LAUNCHD_PLIST.write_text(plist_content)
    print(f"ensure-daemon: wrote {_LAUNCHD_PLIST}")

    # Load
    try:
        result = subprocess.run(
            ["launchctl", "load", "-w", str(_LAUNCHD_PLIST)],
            capture_output=True, timeout=10,
        )
        if result.returncode == 0:
            print(f"ensure-daemon: loaded {_LAUNCHD_LABEL} successfully.")
            return "installed"
        else:
            err = result.stderr.decode(errors="replace").strip()
            print(f"ensure-daemon: launchctl load failed: {err}", file=sys.stderr)
            return "load_failed"
    except Exception as exc:
        print(f"ensure-daemon: launchctl load error: {exc}", file=sys.stderr)
        return "load_failed"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_catch_up(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Deliver new jarvis-signal comments to worktree inboxes.")
    ap.add_argument("--issue", type=int, required=True)
    ap.add_argument("--branch", default=None)
    ap.add_argument("--since", default=None, help="ISO timestamp; defaults to phase cache cursor")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    n = catch_up(args.issue, branch=args.branch, since=args.since, dry_run=args.dry_run)
    print(f"Delivered {n} signal(s).")
    return 0


def _cmd_watch(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Poll for new jarvis-signal comments.")
    ap.add_argument("--issue", type=int, required=True)
    ap.add_argument("--branch", default=None)
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    watch(args.issue, branch=args.branch, interval=args.interval, dry_run=args.dry_run)
    return 0


def _cmd_watch_all(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Always-on daemon: poll all open jarvis-work issues + open PRs."
    )
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    watch_all(interval=args.interval, dry_run=args.dry_run)
    return 0


def _cmd_dispatch(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Dispatch a signal to the owning worktree.")
    ap.add_argument("--branch", required=True)
    ap.add_argument("--event-json", required=True)
    args = ap.parse_args(argv)
    try:
        event = json.loads(args.event_json)
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON: {e}", file=sys.stderr)
        return 2
    result = _dispatch(args.branch, event)
    print(result)
    return 0


def _cmd_ensure_daemon(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Idempotently install and load the launchd daemon for watch-all."
    )
    ap.add_argument("--interval", type=int, default=30)
    args = ap.parse_args(argv)
    status = ensure_daemon(interval=args.interval)
    return 0 if status in ("installed", "already_running") else 1


_COMMANDS = {
    "catch-up": _cmd_catch_up,
    "watch": _cmd_watch,
    "watch-all": _cmd_watch_all,
    "dispatch": _cmd_dispatch,
    "ensure-daemon": _cmd_ensure_daemon,
}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] not in _COMMANDS:
        print(f"Usage: dev_event_listener.py <{'|'.join(_COMMANDS)}> [opts]", file=sys.stderr)
        return 2
    return _COMMANDS[args[0]](args[1:])


if __name__ == "__main__":
    raise SystemExit(main())
