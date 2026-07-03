#!/usr/bin/env python3
"""Route jarvis-signal payloads to the owning worktree inbox (local, no cloud).

Pure, stdlib-only, unit-testable (no network). Reuses phase_state._slug and
_cache_path for consistent filenames — but implements its own None-returning
loader so an absent cache file means "unrouted" (phase_state._load_cache would
fabricate a default dict, losing the "is this branch tracked?" signal).

Inbox format (session-<slug>-pending.jsonl): one JSON object per line, FIFO.
Processed log (session-<slug>-processed.jsonl): same, appended after drain.
Status lock (session-<slug>-status.json): busy/idle state written by hooks.

CLI:
    route --signal-json '<json>' [--author LOGIN]
        Parse and route a signal payload; print the verdict.

    drain --branch <branch>
        Pop the oldest event from the pending inbox (FIFO), print it as JSON,
        and move it to the processed log. Exits 1 when inbox is empty.
        Called by the stop hook when idle to drain queued events.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
METRICS_DIR = REPO_ROOT / "metrics" / "pr_cost"

DEBOUNCE_WINDOW_SEC = 300  # 5 min — collapse ci_failed bursts to one babysit

# Author allowlist for intake signals (OQ-1 / G3 from v1).
# CI/merge signals are emitted by trusted GH Actions workflows, not user comments.
ALLOWED_AUTHORS = {"aditya2kx", "jarvis-agent-bot328"}

# Map signal event → inbox record kind
EVENT_KIND: dict[str, str] = {
    "ci_failed": "babysit_ci",
    "ci_passed": "ci_green",
    "ci_other": "ci_status",
    "pr_merged": "retrospective",
    "intake": "intake",
    "comment": "address_comment",
}

# Events that require an allowlisted author when an author is supplied.
# CI/merge events are emitted by trusted GH Actions workflows (no user-comment gate needed).
AUTHOR_GATED_EVENTS = {"intake", "comment"}


# ---------------------------------------------------------------------------
# Filename helpers (reuse phase_state slugifier for consistency)
# ---------------------------------------------------------------------------

def _slug(branch: str) -> str:
    """Match the slugifier in phase_state.py / post_merge_lifecycle.py."""
    import re
    return re.sub(r"[^a-zA-Z0-9_-]", "-", branch)[:60]


def _phase_path(branch: str) -> Path:
    # Phase cache + delivered-signal dedup stay daemon-side (module METRICS_DIR),
    # never in the child worktree — the daemon owns dedup across all worktrees.
    return METRICS_DIR / f"session-{_slug(branch)}-phase.json"


def _worktree_metrics_dir(cache: dict | None) -> Path:
    """metrics/pr_cost dir the child worktree's drain.sh reads from.

    The daemon routes from the parent repo but the child chat drains its own
    worktree's inbox; writing to the module METRICS_DIR would strand events in
    the parent (obs 4b). When the phase cache records an on-disk worktree, write
    the inbox there so the child sees it; otherwise fall back to the module dir.
    """
    if cache:
        wt = cache.get("worktree_path")
        if wt and Path(wt).is_dir():
            return Path(wt) / "metrics" / "pr_cost"
    return METRICS_DIR


def _inbox_path(branch: str, cache: dict | None = None) -> Path:
    return _worktree_metrics_dir(cache) / f"session-{_slug(branch)}-pending.jsonl"


def _processed_path(branch: str, cache: dict | None = None) -> Path:
    return _worktree_metrics_dir(cache) / f"session-{_slug(branch)}-processed.jsonl"


# ---------------------------------------------------------------------------
# Router-local cache loader
#
# IMPORTANT: do NOT call phase_state._load_cache here. That function fabricates
# a default dict when the file is missing and never returns None, so it cannot
# distinguish "branch not tracked" from "branch tracked but no events yet".
# We need the None semantics to return "unrouted" for unknown branches.
# ---------------------------------------------------------------------------

def _derive_worktree_path(branch: str) -> Path | None:
    """Best-effort sibling worktree path for ``branch`` (``../<repo>-wt-<slug>``
    convention, matches ``new_requirement.py`` / ``dev_event_listener._worktree_path_for``).

    Returns None when no such directory exists on disk.
    """
    if not branch:
        return None
    slug = _slug(branch)
    repo_name = REPO_ROOT.name.split("-wt-")[0] if "-wt-" in REPO_ROOT.name else REPO_ROOT.name
    candidate = REPO_ROOT.parent / f"{repo_name}-wt-{slug}"
    return candidate if candidate.is_dir() else None


def _load_cache(branch: str) -> dict | None:
    """Return the phase-cache dict for ``branch``, or None when not tracked.

    Tries the daemon-side path first (``METRICS_DIR``, shared with
    ``phase_state.py`` when run from this checkout). Falls back to reading
    the branch's own worktree metrics dir when the daemon-side cache is
    missing but the worktree exists on disk — a signal must not go
    "unrouted" merely because the daemon process happens to be running from
    a checkout that never saw ``phase_state.py init`` for this branch
    (Issue #140 defense-in-depth). The worktree's cache is mirrored into the
    daemon-side path so subsequent lookups are fast and consistent.
    """
    path = _phase_path(branch)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return None

    wt = _derive_worktree_path(branch)
    if wt is None:
        return None
    wt_cache = wt / "metrics" / "pr_cost" / f"session-{_slug(branch)}-phase.json"
    if not wt_cache.exists():
        return None
    try:
        data = json.loads(wt_cache.read_text())
    except Exception:
        return None
    _save_cache(branch, data)
    return data


def _save_cache(branch: str, data: dict) -> None:
    import datetime
    data["updated_at"] = datetime.datetime.utcnow().isoformat() + "Z"
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    _phase_path(branch).write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Debounce helper
# ---------------------------------------------------------------------------

def _debounced(cache: dict, now: float) -> bool:
    """True when the last ci_failed delivery was within DEBOUNCE_WINDOW_SEC."""
    last_ts_str = cache.get("last_ci_failed_ts")
    if not last_ts_str:
        return False
    import datetime
    try:
        last = datetime.datetime.fromisoformat(last_ts_str.rstrip("Z")).replace(
            tzinfo=datetime.timezone.utc
        )
        elapsed = now - last.timestamp()
        return elapsed < DEBOUNCE_WINDOW_SEC
    except Exception:
        return False


def _iso(ts: float) -> str:
    import datetime
    return datetime.datetime.utcfromtimestamp(ts).isoformat() + "Z"


# ---------------------------------------------------------------------------
# Inbox helpers
# ---------------------------------------------------------------------------

def _append_inbox(branch: str, record: dict, cache: dict | None = None) -> None:
    inbox = _inbox_path(branch, cache)
    inbox.parent.mkdir(parents=True, exist_ok=True)
    with inbox.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _mark_delivered(branch: str, sid: str, now: float, event: str, cache: dict) -> None:
    delivered = list(cache.get("delivered_signals", []))
    if sid and sid not in delivered:
        delivered.append(sid)
    cache["delivered_signals"] = delivered
    cache["last_signal_cursor"] = _iso(now)
    if event == "ci_failed":
        cache["last_ci_failed_ts"] = _iso(now)
    # Increment pending count
    cache["pending_event_count"] = cache.get("pending_event_count", 0) + 1
    _save_cache(branch, cache)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def route_signal(
    signal: dict,
    *,
    author: str | None = None,
    now: float | None = None,
) -> str:
    """Route a parsed jarvis-signal payload to the owning worktree inbox.

    Returns one of:
        delivered     — signal written to inbox; cache updated
        duplicate     — signal id already in delivered_signals; skipped
        debounced     — ci_failed within DEBOUNCE_WINDOW_SEC of previous; skipped
        unrouted      — no phase cache for this branch (branch not tracked locally)
        unauthorized  — intake/comment signal from non-allowlisted author
    """
    branch = signal.get("branch")
    sid = signal.get("id")
    event = signal.get("event")
    now = now or time.time()

    # Intake/comment signals are gated by author allowlist.
    # Intake: no author == unauthorized (strict; the router is the primary gate).
    # Comment: reject only when author is explicitly provided and not allowlisted
    #          (the workflow is the primary gate; no-author comment passes through).
    # CI/merge signals come from trusted GH Actions workflows (no author gate).
    if event == "intake" and author not in ALLOWED_AUTHORS:
        return "unauthorized"
    if event == "comment" and author is not None and author not in ALLOWED_AUTHORS:
        return "unauthorized"

    if not branch:
        return "unrouted"

    cache = _load_cache(branch)
    if cache is None:
        return "unrouted"

    delivered = cache.get("delivered_signals", [])
    if sid and sid in delivered:
        return "duplicate"

    if event == "ci_failed" and _debounced(cache, now):
        return "debounced"

    kind = EVENT_KIND.get(event, event)
    _append_inbox(branch, {"kind": kind, **signal}, cache)
    _mark_delivered(branch, sid or "", now, event or "", cache)
    return "delivered"


def drain(branch: str) -> dict | None:
    """Pop the oldest event from the pending inbox (FIFO).

    Moves the record to the processed log and decrements pending_event_count
    in the phase cache. Returns the event dict, or None when the inbox is empty.
    """
    # Resolve the inbox from the phase cache so drain reads the same worktree
    # dir route_signal wrote to (obs 4b). When run inside the child worktree the
    # local cache's worktree_path points at itself, so this stays consistent.
    cache = _load_cache(branch)
    inbox = _inbox_path(branch, cache)
    if not inbox.exists():
        return None

    lines = inbox.read_text(encoding="utf-8").splitlines(keepends=True)
    if not lines:
        return None

    # Pop oldest (first line)
    first_line = lines[0]
    remaining = lines[1:]
    inbox.write_text("".join(remaining), encoding="utf-8")

    try:
        record = json.loads(first_line)
    except Exception:
        record = {"raw": first_line.strip()}

    # Append to processed log (same worktree dir as the inbox)
    processed = _processed_path(branch, cache)
    processed.parent.mkdir(parents=True, exist_ok=True)
    with processed.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    # Decrement pending count in the (daemon-side) phase cache.
    if cache is not None:
        count = cache.get("pending_event_count", 0)
        cache["pending_event_count"] = max(0, count - 1)
        _save_cache(branch, cache)

    return record


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_route(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Route a jarvis-signal payload to the owning worktree inbox.")
    ap.add_argument("--signal-json", required=True, help="JSON string of the signal payload")
    ap.add_argument("--author", default=None, help="GitHub login of the comment author (for intake guard)")
    args = ap.parse_args(argv)
    try:
        signal = json.loads(args.signal_json)
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON: {e}", file=sys.stderr)
        return 2
    verdict = route_signal(signal, author=args.author)
    print(verdict)
    return 0


def _cmd_drain(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Pop the oldest pending event from the worktree inbox.")
    ap.add_argument("--branch", required=True)
    args = ap.parse_args(argv)
    record = drain(args.branch)
    if record is None:
        return 1  # empty inbox
    print(json.dumps(record))
    return 0


_COMMANDS = {
    "route": _cmd_route,
    "drain": _cmd_drain,
}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] not in _COMMANDS:
        print(f"Usage: dev_event_router.py <{'|'.join(_COMMANDS)}> [opts]", file=sys.stderr)
        return 2
    return _COMMANDS[args[0]](args[1:])


if __name__ == "__main__":
    raise SystemExit(main())
