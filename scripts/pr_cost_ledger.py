#!/usr/bin/env python3
"""Per-PR token / model / cost ledger — the BHAGA cost-monitoring framework.

Tracks the full cost of landing a change, from the moment a requirement is
picked up until the PR merges, across BOTH cost surfaces:

  * BUILD  — the Cursor agent sessions that wrote the code (model =
             claude-opus-*, etc.). Auto path: `capture-build` pulls exact
             per-request token+cost from the Cursor usage API via
             `cursor_usage.py` (local session token). Manual fallback:
             `record-build` from the dashboard UI — marked approximate.
             PR attribution: auto-window from `ai-code-tracking.db` (edit-
             anchored, capped at merge) or explicit `--start/--end`.
  * REVIEW — the Claude PR-review GitHub Action (model = claude-sonnet-*).
             Exact: each run posts a cost comment (from its `execution_file`).
             The CI-side ledger append is ephemeral (runner FS is discarded;
             committing back would loop the review), so `capture-review` rebuilds
             the rows from those posted comments at the same pre-merge checkpoint
             as build — committed once by the operator.

Data source (committed in-repo, the "some data source" the spec asks for):
    metrics/pr_cost/PR-<n>.json    — one record per PR.

CLI:
    # pre-merge gate (CI): fail if the PR has no cost record / no review data
    python3 scripts/pr_cost_ledger.py validate --pr 12

    # post-merge: top cost areas + efficiency recommendations
    python3 scripts/pr_cost_ledger.py analyze --pr 12
    python3 scripts/pr_cost_ledger.py analyze            # across all PRs

    # manual entry (build rows from the Cursor dashboard)
    python3 scripts/pr_cost_ledger.py record-build --pr 12 \\
        --ts 2026-06-02T12:56-05:00 --tokens 44000000 --cost 30.76 \\
        --model claude-opus-4-8-thinking-high

    python3 scripts/pr_cost_ledger.py set-meta --pr 12 --title "…" \\
        --requirement "…" --branch "…" --created 2026-06-02T18:27:22Z
"""

from __future__ import annotations

import argparse
import datetime
import glob
import html
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

LEDGER_DIR = Path(__file__).resolve().parent.parent / "metrics" / "pr_cost"

# A build session is considered the "dominant" cost hotspot when it alone is at
# least this fraction of total build cost — the single thing most worth fixing.
_DOMINANT_SESSION_FRACTION = 0.40
# Review is "expensive relative to its job" if it crosses this many $ per run.
_REVIEW_RUN_WARN = 3

# ── Model rate table (Cursor per-1M-token prices, verified 2026-06-03) ──────
# https://cursor.com/docs/models-and-pricing — Cursor charges at API rates, no markup.
# Update the date comment when you verify rates are still current.
_MODEL_RATES: dict[str, dict[str, float]] = {
    # key matched against lowercase model name by _model_tier()
    "opus":     {"input": 5.00, "cache_write": 6.25, "cache_read": 0.50, "output": 25.00},
    "sonnet":   {"input": 3.00, "cache_write": 3.75, "cache_read": 0.30, "output": 15.00},
    "haiku":    {"input": 1.00, "cache_write": 1.25, "cache_read": 0.10, "output":  5.00},
    "composer": {"input": 0.50, "cache_write": 0.00, "cache_read": 0.20, "output":  2.50},
}
_RATES_DATE = "2026-06-03"


def _model_tier(model: str) -> str:
    """Map a raw model string to a tier key in _MODEL_RATES."""
    m = (model or "").lower()
    if "opus" in m:
        return "opus"
    if "sonnet" in m:
        return "sonnet"
    if "haiku" in m:
        return "haiku"
    return "composer"


def _recompute_cost(session: dict[str, Any], tier: str) -> float:
    """Estimate what this session would cost on a different model tier."""
    r = _MODEL_RATES[tier]
    inp  = int(session.get("input_tokens") or 0)
    outp = int(session.get("output_tokens") or 0)
    cr   = int(session.get("cache_read_input_tokens") or 0)
    cw   = int(session.get("cache_creation_input_tokens") or 0)
    if not (inp + outp + cr + cw):
        # No token breakdown — fall back to a simple cache-read-dominant estimate
        tokens = int(session.get("tokens") or 0)
        return tokens * r["cache_read"] / 1_000_000
    return (inp * r["input"] + outp * r["output"]
            + cr * r["cache_read"] + cw * r["cache_write"]) / 1_000_000


# ── record I/O ──────────────────────────────────────────────────────
#
# A ledger record is keyed by the GitHub PR number once it exists
# (`PR-<n>.json`). But the PR number is only assigned when `gh pr create`
# actually runs — and several chat spaces may be opening PRs concurrently, so
# the number cannot be guessed up front. Before the PR exists, a session is
# tracked by a *provisional* key (the branch name) in `session-<slug>.json`,
# which is invisible to `_all_prs()` / the report / the cost gate (they only
# match `PR-<int>.json`). `bind_pr()` renames the provisional record to
# `PR-<n>.json` once the real number is known. See `start_pr_session.py`.


def _slug(text: str) -> str:
    """Filename-safe slug for a provisional (branch-keyed) record."""
    out = re.sub(r"[^A-Za-z0-9._-]+", "-", str(text).strip()).strip("-._")
    return out or "session"


def _record_path(key: int | str) -> Path:
    """Resolve a record key to its on-disk path.

    Numeric key → `PR-<n>.json` (a real PR). Non-numeric key → a provisional
    `session-<slug>.json` (branch-keyed, before the PR number is assigned).
    """
    if isinstance(key, int) or (isinstance(key, str) and key.isdigit()):
        return LEDGER_DIR / f"PR-{int(key)}.json"
    return LEDGER_DIR / f"session-{_slug(str(key))}.json"


def _empty_record(pr: int | str) -> dict[str, Any]:
    is_numeric = isinstance(pr, int) or (isinstance(pr, str) and pr.isdigit())
    return {
        "pr_number": int(pr) if is_numeric else None,
        "provisional_id": None if is_numeric else str(pr),
        "title": None,
        "requirement": None,
        "branch": None,
        "created_at": None,
        "merged_at": None,
        "diff": {"files": None, "additions": None, "deletions": None},
        "build": {
            "source": None,
            "approximate": True,
            "attribution_mode": None,
            "session_started_at": None,
            "conversation_ids": [],
            "sessions": [],
            "tokens_total": 0,
            "cost_usd_total": 0.0,
        },
        "review": {
            "source": "claude-code-action execution_file (exact)",
            "runs": [],
            "tokens_total": 0,
            "cost_usd_total": 0.0,
            "run_count": 0,
        },
        "totals": {"tokens": 0, "cost_usd": 0.0},
    }


def load_record(pr: int | str) -> dict[str, Any]:
    path = _record_path(pr)
    if path.is_file():
        return json.loads(path.read_text())
    return _empty_record(pr)


def _record_key(rec: dict[str, Any]) -> int | str:
    """The on-disk key for a record: the PR number if assigned, else the
    provisional id (branch-keyed session)."""
    if rec.get("pr_number") is not None:
        return int(rec["pr_number"])
    pid = rec.get("provisional_id")
    if not pid:
        raise ValueError("record has neither pr_number nor provisional_id")
    return str(pid)


def save_record(rec: dict[str, Any]) -> Path:
    _recompute_totals(rec)
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    path = _record_path(_record_key(rec))
    path.write_text(json.dumps(rec, indent=2, sort_keys=False) + "\n")
    return path


def _recompute_totals(rec: dict[str, Any]) -> None:
    b = rec["build"]
    b["tokens_total"] = sum(int(s.get("tokens") or 0) for s in b["sessions"])
    b["cost_usd_total"] = round(sum(float(s.get("cost_usd") or 0) for s in b["sessions"]), 4)
    r = rec["review"]
    r["run_count"] = len(r["runs"])
    r["tokens_total"] = sum(int(x.get("tokens") or 0) for x in r["runs"])
    r["cost_usd_total"] = round(sum(float(x.get("cost_usd") or 0) for x in r["runs"]), 4)
    rec["totals"] = {
        "tokens": b["tokens_total"] + r["tokens_total"],
        "cost_usd": round(b["cost_usd_total"] + r["cost_usd_total"], 4),
    }


# ── mutation helpers (used by CLI + post_claude_review_cost) ─────────

def set_meta(pr: int, **fields: Any) -> dict[str, Any]:
    rec = load_record(pr)
    for k in ("title", "requirement", "branch", "created_at", "merged_at", "session_started_at"):
        if fields.get(k) is not None:
            rec[k] = fields[k]
    if fields.get("conversation_ids") is not None:
        rec["build"]["conversation_ids"] = list(fields["conversation_ids"])
    for dk in ("files", "additions", "deletions"):
        if fields.get(dk) is not None:
            rec["diff"][dk] = int(fields[dk])
    save_record(rec)
    return rec


def bind_conversations(
    pr: int, *,
    conversation_ids: list[str] | None = None,
    auto: bool = False,
    session_started_at: str | None = None,
) -> dict[str, Any]:
    """Bind one or more Cursor chat spaces (conversationId) to a PR ledger."""
    import cursor_usage  # noqa: PLC0415

    rec = load_record(pr)
    if session_started_at:
        rec["session_started_at"] = session_started_at
    if not rec.get("session_started_at") and not auto and not conversation_ids:
        raise SystemExit(
            "bind-conversation: pass --session-started, --auto, or explicit --conversation-id"
        )
    end_ms = cursor_usage.to_ms(rec["merged_at"]) if rec.get("merged_at") else int(
        datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000
    )
    start_ms = cursor_usage.to_ms(rec["session_started_at"]) if rec.get("session_started_at") else None
    if auto:
        if start_ms is None:
            branch = rec.get("branch")
            if branch:
                try:
                    start_ms, _ = cursor_usage.derive_window_for_branch(
                        branch, merged_at=rec.get("merged_at")
                    )
                except cursor_usage.CursorUsageError:
                    try:
                        start_ms, _ = cursor_usage.git_branch_commit_range_ms(branch)
                    except cursor_usage.CursorUsageError:
                        start_ms = None
            if start_ms is None:
                raise SystemExit(
                    f"bind-conversation --auto: set session_started_at via start_pr_session "
                    f"or set-meta --pr {pr} --session-started <ISO>"
                )
        ids = cursor_usage.auto_bind_conversations(start_ms, end_ms)
        if not ids:
            raise SystemExit(
                f"bind-conversation --auto: no conversation activity for PR #{pr} "
                f"after {rec.get('session_started_at') or 'derived start'}"
            )
        rec["build"]["conversation_ids"] = ids
    elif conversation_ids is not None:
        rec["build"]["conversation_ids"] = list(conversation_ids)
    save_record(rec)
    return rec


def _resolve_pr_number_from_branch(branch: str, *, repo: str | None = None) -> int | None:
    """Look up the GitHub PR number for a head branch via `gh` (None if unknown)."""
    args = ["gh", "pr", "list", "--head", branch, "--state", "all",
            "--json", "number", "--limit", "1"]
    if repo:
        args += ["--repo", repo]
    try:
        out = subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL).strip()
        data = json.loads(out or "[]")
        return int(data[0]["number"]) if data else None
    except Exception:  # noqa: BLE001 — gh missing / not authed / no match
        return None


def bind_pr(
    provisional: str, *, pr_number: int | None = None,
    branch: str | None = None, repo: str | None = None,
) -> dict[str, Any]:
    """Promote a provisional (branch-keyed) session record to `PR-<n>.json`.

    The PR number only exists after `gh pr create` runs, so a session started
    before that lives in `session-<slug>.json`. This renames it to the real
    `PR-<n>.json`, preserving the captured build cost. If a `PR-<n>.json`
    already exists (e.g. review cost was recorded by CI first), the provisional
    build sessions + metadata are merged in and the existing review runs kept.
    """
    prov_path = _record_path(provisional)
    if not prov_path.is_file():
        prov_path = _record_path(_slug(provisional))
    if not prov_path.is_file():
        raise SystemExit(
            f"bind-pr: no provisional session record for '{provisional}' "
            f"(looked for {prov_path.name}). Did you start the session with "
            f"start_pr_session.py --branch {provisional!r}?"
        )
    prov = json.loads(prov_path.read_text())
    br = branch or prov.get("branch") or provisional
    if pr_number is None:
        pr_number = _resolve_pr_number_from_branch(br, repo=repo)
    if pr_number is None:
        raise SystemExit(
            f"bind-pr: could not resolve a PR number for branch '{br}'. "
            f"Open the PR first (`gh pr create`), then pass --pr <n> explicitly."
        )

    merged = load_record(int(pr_number))  # existing PR-<n>.json or a fresh record
    for k in ("title", "requirement", "branch", "created_at", "session_started_at"):
        if prov.get(k):
            merged[k] = prov[k]
    # The provisional record is where the build cost was captured — it wins.
    if prov["build"].get("sessions") or not merged["build"].get("sessions"):
        merged["build"] = prov["build"]
    merged["pr_number"] = int(pr_number)
    merged["provisional_id"] = None
    out_path = save_record(merged)

    # Remove the provisional record + its brief/launcher so the namespace is clean.
    prov_path.unlink(missing_ok=True)
    slug = _slug(prov.get("provisional_id") or br)
    for sidecar in (LEDGER_DIR / f"session-{slug}-brief.md",
                    LEDGER_DIR / f"session-{slug}-launch.html"):
        sidecar.unlink(missing_ok=True)
    print(f"bind-pr: {prov_path.name} → {out_path.name} (PR #{pr_number}, "
          f"build ${merged['build']['cost_usd_total']:.2f})")
    return merged


def _session_row_from_event(e: dict[str, Any]) -> dict[str, Any]:
    notes: list[str] = []
    if e.get("is_headless"):
        notes.append("headless")
    if e.get("cost_source") == "byok_token_usage":
        notes.append("byok")
    if e.get("conversation_id"):
        notes.append(f"chat:{e['conversation_id'][:8]}")
    return {
        "ts": e["ts_iso"], "model": e["model"], "tokens": e["tokens"],
        "cost_usd": e["cost_usd"],
        "cost_source": e.get("cost_source"),
        "conversation_id": e.get("conversation_id"),
        "input_tokens": e["input_tokens"], "output_tokens": e["output_tokens"],
        "cache_read_input_tokens": e["cache_read"],
        "cache_creation_input_tokens": e["cache_write"],
        "note": "; ".join(notes) if notes else None,
    }


def capture_build(
    pr: int, *, start: str | None = None, end: str | None = None,
    model_filter: str | None = None,
    conversation_auto: bool = False,
    allow_wide_manual: bool = False,
) -> dict[str, Any]:
    """Auto-fill build sessions from the Cursor usage API.

    Attribution priority:
      1. conversation — bound conversation_ids (+ optional session_started_at)
      2. branch_window — ai-code-tracking.db commit/edit window
      3. manual — explicit --start/--end (rejected if >4h unless approximate)
    """
    import cursor_usage  # noqa: PLC0415

    rec0 = load_record(pr)
    attribution_mode = "branch_window"
    start_ms: int | None = None
    end_ms: int | None = None

    conv_ids = list(rec0["build"].get("conversation_ids") or [])
    if conversation_auto or (not conv_ids and rec0.get("session_started_at")):
        bind_conversations(pr, auto=True)
        rec0 = load_record(pr)
        conv_ids = list(rec0["build"].get("conversation_ids") or [])

    if conv_ids:
        attribution_mode = "conversation"
        end_ms = (
            cursor_usage.to_ms(rec0["merged_at"]) if rec0.get("merged_at")
            else int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
        )
        if rec0.get("session_started_at"):
            start_ms = cursor_usage.to_ms(rec0["session_started_at"])
        elif start and end:
            start_ms, end_ms = cursor_usage.to_ms(start), cursor_usage.to_ms(end)
        else:
            branch = rec0.get("branch")
            if branch:
                try:
                    start_ms, end_ms = cursor_usage.git_branch_commit_range_ms(branch)
                except cursor_usage.CursorUsageError:
                    start_ms = end_ms - 3600_000
            else:
                start_ms = end_ms - 3600_000
    elif start and end:
        attribution_mode = "manual_window"
        start_ms, end_ms = cursor_usage.to_ms(start), cursor_usage.to_ms(end)
        span = cursor_usage.manual_window_span_ms(start, end)
        if span > cursor_usage._MAX_MANUAL_WINDOW_MS and not allow_wide_manual:
            raise SystemExit(
                f"capture-build: manual window is {span / 3600_000:.1f}h — too wide for "
                f"parallel-chat attribution. Use start_pr_session + conversation bind, "
                f"or pass --allow-wide-manual to mark approximate."
            )
    else:
        if start or end:
            raise SystemExit(
                "capture-build: --start and --end must be provided together, "
                "or both omitted for auto-derive."
            )
        branch = rec0.get("branch")
        if not branch:
            raise SystemExit(
                f"PR #{pr} has no branch recorded — run `set-meta --pr {pr} --branch <name>` "
                f"or pass --start/--end explicitly."
            )
        try:
            start_ms, end_ms = cursor_usage.derive_window_for_branch(
                branch, merged_at=rec0.get("merged_at")
            )
        except cursor_usage.CursorUsageError:
            start_ms, end_ms = cursor_usage.git_branch_commit_range_ms(branch)

    assert start_ms is not None and end_ms is not None
    events = cursor_usage.fetch_usage_events(start_ms, end_ms)
    if conv_ids:
        events = cursor_usage.filter_events_for_conversations(
            events, conv_ids, start_ms, end_ms,
        )
    if model_filter:
        events = [e for e in events if model_filter.lower() in (e.get("model") or "").lower()]

    rec = load_record(pr)
    rec["build"]["attribution_mode"] = attribution_mode
    rec["build"]["approximate"] = attribution_mode == "manual_window"
    rec["build"]["source"] = (
        "cursor dashboard usage API (conversation-scoped via ai_code_hashes)"
        if attribution_mode == "conversation"
        else "cursor dashboard usage API (local session token; request->PR by time window)"
    )
    rec["build"]["window"] = {
        "start": datetime.datetime.fromtimestamp(start_ms / 1000, datetime.timezone.utc).isoformat(),
        "end": datetime.datetime.fromtimestamp(end_ms / 1000, datetime.timezone.utc).isoformat(),
    }
    if conv_ids:
        rec["build"]["conversation_ids"] = conv_ids
    rec["build"]["sessions"] = [_session_row_from_event(e) for e in events]
    rec["build"]["sessions"].sort(key=lambda s: s.get("ts") or "")
    save_record(rec)
    return rec


def record_build_session(
    pr: int, *, ts: str, tokens: int, cost_usd: float,
    model: str, source: str | None = None, note: str | None = None,
) -> dict[str, Any]:
    rec = load_record(pr)
    if source:
        rec["build"]["source"] = source
    # De-dupe by timestamp so re-imports of the dashboard are idempotent.
    rec["build"]["sessions"] = [s for s in rec["build"]["sessions"] if s.get("ts") != ts]
    rec["build"]["sessions"].append({
        "ts": ts, "model": model, "tokens": int(tokens),
        "cost_usd": round(float(cost_usd), 4), "note": note,
    })
    rec["build"]["sessions"].sort(key=lambda s: s.get("ts") or "")
    save_record(rec)
    return rec


def _review_fingerprint(row: dict[str, Any]) -> tuple:
    """Stable identity for a review run lacking both run_url and ts."""
    return (
        row.get("model"), row.get("turns"), row.get("result"),
        row.get("input_tokens"), row.get("output_tokens"),
        row.get("cache_read_input_tokens"), row.get("cache_creation_input_tokens"),
        row.get("cost_usd"),
    )


def record_review_run(
    pr: int, *, ts: str | None, model: str, turns: int | None,
    input_tokens: int, output_tokens: int, cache_read: int, cache_write: int,
    cost_usd: float | None, result: str | None, run_url: str | None = None,
) -> dict[str, Any]:
    """Append one Claude-review run. Called by post_claude_review_cost.py."""
    rec = load_record(pr)
    tokens = int(input_tokens) + int(output_tokens) + int(cache_read) + int(cache_write)
    entry = {
        "ts": ts, "model": model, "turns": turns,
        "input_tokens": int(input_tokens), "output_tokens": int(output_tokens),
        "cache_read_input_tokens": int(cache_read),
        "cache_creation_input_tokens": int(cache_write),
        "tokens": tokens,
        "cost_usd": round(float(cost_usd), 4) if cost_usd is not None else None,
        "result": result, "run_url": run_url,
    }
    # De-dupe by run_url (one cost row per workflow run), then ts, then a stable
    # content fingerprint when neither identifier is present (e.g. a cost comment
    # posted before --workflow-run-url was wired). The fingerprint keeps
    # capture-review idempotent for those legacy/link-less comments.
    if run_url:
        rec["review"]["runs"] = [x for x in rec["review"]["runs"] if x.get("run_url") != run_url]
    elif ts:
        rec["review"]["runs"] = [x for x in rec["review"]["runs"] if x.get("ts") != ts]
    else:
        fp = _review_fingerprint(entry)
        rec["review"]["runs"] = [
            x for x in rec["review"]["runs"]
            if x.get("run_url") or x.get("ts") or _review_fingerprint(x) != fp
        ]
    rec["review"]["runs"].append(entry)
    rec["review"]["runs"].sort(key=lambda x: x.get("ts") or "")
    save_record(rec)
    return rec


# ── review capture from posted PR cost comments ─────────────────────
#
# The CI append (post_claude_review_cost.py writing the ledger on the runner) is
# ephemeral — the runner filesystem is discarded. Committing back inside the
# review workflow would re-trigger `synchronize` → another review → another
# commit (an infinite cost loop), so we DON'T do that. Instead the durable record
# of each review run is its posted PR cost comment; `capture-review` reconstructs
# the ledger rows from those comments at the same pre-merge checkpoint where build
# cost is captured, so the operator commits the complete record once.

_NUM = r"([\d,]+)"
_COST = r"\*?\*?~?\$([\d.]+)"


def _parse_cost_comment(body: str) -> dict[str, Any] | None:
    """Parse one `### Claude review — API cost` comment body into a run dict.

    Returns None for the bootstrap/"review did not run" comments (no real data).
    """
    if "Claude review — API cost" not in body:
        return None
    if "No execution file was produced" in body or "Review did not run" in body:
        return None

    def _row(label_re: str, pat: str = _NUM) -> str | None:
        m = re.search(rf"\|\s*{label_re}\s*\|\s*{pat}", body)
        return m.group(1) if m else None

    def _int(label_re: str) -> int:
        v = _row(label_re)
        return int(v.replace(",", "")) if v else 0

    model_m = re.search(r"\|\s*Model\s*\|\s*`([^`]+)`", body)
    cost_m = re.search(rf"\|\s*\*?\*?(?:Reported|Estimated) cost\*?\*?\s*\|\s*{_COST}", body)
    run_m = re.search(r"\[Workflow run\]\((https?://[^)]+)\)", body)
    result_m = re.search(r"\|\s*Run result\s*\|\s*`([^`]+)`", body)
    turns = _row(r"Turns")
    return {
        "model": model_m.group(1) if model_m else "claude-sonnet-4-6",
        "turns": int(turns) if turns else None,
        "input_tokens": _int(r"Input tokens \(uncached\)"),
        "output_tokens": _int(r"Output tokens"),
        "cache_read": _int(r"Cache read tokens[^|]*"),
        "cache_write": _int(r"Cache write tokens[^|]*"),
        "cost_usd": float(cost_m.group(1)) if cost_m else None,
        "result": result_m.group(1) if result_m else None,
        "run_url": run_m.group(1) if run_m else None,
    }


def _fetch_pr_comment_bodies(pr: int, repo: str | None) -> list[str]:
    cmd = ["gh", "api", f"repos/{repo}/issues/{pr}/comments", "--paginate",
           "--jq", ".[].body"] if repo else \
          ["gh", "api", f"repos/{{owner}}/{{repo}}/issues/{pr}/comments", "--paginate",
           "--jq", ".[].body"]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    # --jq .[].body emits one (possibly multi-line) body per record; bodies are
    # separated by newlines but a body can contain newlines, so split on the header.
    chunks = out.split("### Claude review — API cost")
    return ["### Claude review — API cost" + c for c in chunks[1:]]


def capture_review(pr: int, *, repo: str | None = None) -> dict[str, Any]:
    """Reconstruct review-cost rows from the PR's posted cost comments (via gh).

    Idempotent: dedups by workflow run URL, so re-running after more review
    rounds just adds the new runs.
    """
    for body in _fetch_pr_comment_bodies(pr, repo):
        parsed = _parse_cost_comment(body)
        if not parsed:
            continue
        record_review_run(pr, ts=None, **parsed)
    return load_record(pr)


# ── pre-merge gate ──────────────────────────────────────────────────

def validate(pr: int, *, require_build: bool = False) -> tuple[bool, list[str]]:
    """Pre-merge gate: a PR may not merge without an accounted cost record.

    Requires: the committed record exists, has a requirement/title, and has at
    least one cost surface recorded. With ``require_build`` (the hard CI gate),
    build sessions MUST be present — the author has to record the Cursor build
    cost (manually from the dashboard, or via the capture tooling) and commit
    ``metrics/pr_cost/PR-<n>.json`` before the PR can merge.
    """
    path = _record_path(pr)
    if not path.is_file():
        return False, [f"no cost record at metrics/pr_cost/PR-{pr}.json — "
                       f"run `pr_cost_ledger.py set-meta --pr {pr} …` + record build cost, "
                       f"then commit the file"]
    rec = json.loads(path.read_text())
    problems: list[str] = []
    if not rec.get("requirement") and not rec.get("title"):
        problems.append("record has no requirement/title")
    has_review = bool(rec["review"]["runs"])
    has_build = bool(rec["build"]["sessions"])
    if not has_review and not has_build:
        problems.append("no build sessions and no review runs recorded — cost is unaccounted")
    if require_build and not has_build:
        problems.append(
            "no build sessions recorded — the hard gate requires the Cursor build cost. "
            f"Run `start_pr_session.py --pr {pr}` then `pr_cost_ledger.py sync --pr {pr}` "
            f"before merge."
        )
    b = rec.get("build") or {}
    window = b.get("window") or {}
    if window.get("start") and window.get("end"):
        import cursor_usage  # noqa: PLC0415
        span = cursor_usage.manual_window_span_ms(window["start"], window["end"])
        if (
            b.get("attribution_mode") == "manual_window"
            and span > cursor_usage._MAX_MANUAL_WINDOW_MS
            and not b.get("approximate")
        ):
            problems.append(
                f"build window is {span / 3600_000:.1f}h with manual attribution — "
                "likely includes parallel chat spaces; re-capture with start_pr_session + sync"
            )
    if rec.get("session_started_at") and b.get("attribution_mode") != "conversation":
        problems.append(
            "session_started_at is set but build was not conversation-scoped — "
            f"run `pr_cost_ledger.py sync --pr {pr}` after binding the chat"
        )
    return (not problems), problems


# ── post-merge analysis ─────────────────────────────────────────────

def _observations(rec: dict[str, Any]) -> list[str]:
    """Diagnostic observations — facts about where cost went, not actions."""
    obs: list[str] = []
    b, r = rec["build"], rec["review"]
    total = rec["totals"]["cost_usd"] or 1.0

    # Attribution mode
    mode = b.get("attribution_mode")
    convs = b.get("conversation_ids") or []
    if mode == "conversation" and convs:
        obs.append(
            f"Build attributed to {len(convs)} chat space(s): "
            + ", ".join(c[:8] + "…" for c in convs)
            + " (conversation-scoped via ai_code_hashes + model tier)."
        )
    elif mode == "manual_window":
        obs.append(
            "Build used a manual time window — may include parallel chat spaces if wide."
        )
    build_pct = b["cost_usd_total"] / total * 100
    obs.append(
        f"Build is {build_pct:.0f}% of total cost (${b['cost_usd_total']:.2f}) vs "
        f"review {100 - build_pct:.0f}% (${r['cost_usd_total']:.2f}). "
        f"{'Optimization leverage is in the build loop.' if build_pct >= 70 else 'Build and review costs are comparable.'}"
    )

    # Cache-read dominance (the single biggest surprise in the data)
    if b["sessions"]:
        total_build_tokens = b["tokens_total"] or 1
        cache_tokens = sum(int(s.get("cache_read_input_tokens") or 0) for s in b["sessions"])
        cache_pct = cache_tokens / total_build_tokens * 100
        if cache_pct >= 50:
            obs.append(
                f"Cache reads are {cache_pct:.0f}% of build tokens ({cache_tokens:,} tok) — "
                f"context re-reads, not new work. Each agent turn re-reads the entire "
                f"conversation history as cache-read tokens. Start a fresh chat per PR to "
                f"reset this counter."
            )

    # Model mix
    if b["sessions"]:
        by_tier: dict[str, float] = {}
        for s in b["sessions"]:
            t = _model_tier(s.get("model") or "")
            by_tier[t] = by_tier.get(t, 0.0) + float(s.get("cost_usd") or 0)
        mix = sorted(by_tier.items(), key=lambda x: x[1], reverse=True)
        mix_str = ", ".join(f"{t} ${v:.2f} ({v/b['cost_usd_total']*100:.0f}%)" for t, v in mix if b["cost_usd_total"])
        obs.append(f"Model mix: {mix_str}.")

    # Dominant single session
    if b["sessions"] and b["cost_usd_total"]:
        top = max(b["sessions"], key=lambda s: s.get("cost_usd") or 0)
        frac = float(top.get("cost_usd") or 0) / b["cost_usd_total"]
        if frac >= _DOMINANT_SESSION_FRACTION:
            obs.append(
                f"One session ({(top.get('ts') or '')[:19]}, ${top.get('cost_usd', 0):.2f}, "
                f"{top.get('tokens', 0):,} tok) is {frac*100:.0f}% of build cost — "
                f"a marathon thread with an ever-growing context."
            )

    # Review $/run trend
    if r["run_count"] >= 2:
        per_run = r["cost_usd_total"] / r["run_count"]
        obs.append(
            f"Review: {r['run_count']} runs × ~${per_run:.2f}/run = ${r['cost_usd_total']:.2f}. "
            f"Convergence policy (inline = blocking only, delta re-review) is active since PR #13."
        )
    elif r["run_count"] == 1:
        obs.append(f"Review ran once (${r['cost_usd_total']:.2f}) — ideal.")

    return obs


def _recommendations(rec: dict[str, Any]) -> list[str]:
    """Tactical actions you can take, each with an estimated $ saving."""
    recs: list[str] = []
    b, r = rec["build"], rec["review"]

    # 1. Context discipline (new chat per PR) — only if cache-read dominated
    if b["sessions"] and b["tokens_total"]:
        cache_tokens = sum(int(s.get("cache_read_input_tokens") or 0) for s in b["sessions"])
        cache_pct = cache_tokens / b["tokens_total"] * 100
        if cache_pct >= 70:
            # Rough saving: if context had been reset mid-session, cache-read tokens
            # would be ~50% lower on average — conservative model-independent estimate.
            est_saving = b["cost_usd_total"] * 0.30
            recs.append(
                f"Start a new Cursor chat for each PR/requirement (run "
                f"`scripts/start_pr_session.py --pr {rec['pr_number']}` to get a seeded "
                f"brief + cursor:// link). Each turn re-reads the entire history as "
                f"cache-read tokens; a fresh chat resets the counter. "
                f"Est. saving: ~${est_saving:.2f} ({30:.0f}% of build)."
            )

    # 2. Model routing — per Opus session that looks mechanical
    if b["sessions"]:
        opus_sessions = [s for s in b["sessions"] if _model_tier(s.get("model") or "") == "opus"
                         and float(s.get("cost_usd") or 0) >= 0.50]
        if opus_sessions:
            total_opus = sum(float(s.get("cost_usd") or 0) for s in opus_sessions)
            total_sonnet = sum(_recompute_cost(s, "sonnet") for s in opus_sessions)
            saving = total_opus - total_sonnet
            sessions_desc = ", ".join(
                f"{(s.get('ts') or '')[:19]} (${s.get('cost_usd', 0):.2f})"
                for s in sorted(opus_sessions, key=lambda s: s.get("cost_usd") or 0, reverse=True)[:3]
            )
            recs.append(
                f"Route standard feature work to Sonnet 4.6 (default) — reserve Opus 4.8 "
                f"only for hard multi-file reasoning or subtle bugs. "
                f"{len(opus_sessions)} Opus session(s) ({sessions_desc}) cost ${total_opus:.2f}; "
                f"same work on Sonnet ≈ ${total_sonnet:.2f}. "
                f"Est. saving: ~${saving:.2f}. "
                f"Rates (verified {_RATES_DATE}): Opus cache-read $0.50/M vs Sonnet $0.30/M; "
                f"output $25/M vs $15/M."
            )

    # 3. Thinking effort — flag high-effort when medium likely sufficient
    high_sessions = [s for s in b["sessions"] if "thinking-high" in (s.get("model") or "").lower()
                     and float(s.get("cost_usd") or 0) >= 0.50]
    if high_sessions:
        total_high = sum(float(s.get("cost_usd") or 0) for s in high_sessions)
        # thinking-high vs medium: output tokens (thinking) roughly 30% more on high
        est_saving = total_high * 0.20
        recs.append(
            f"Use thinking=medium instead of thinking=high for non-hard sessions. "
            f"{len(high_sessions)} session(s) used thinking-high (${total_high:.2f} total). "
            f"Medium is indistinguishable for routine feature work; high adds ~20–30% output tokens. "
            f"Est. saving: ~${est_saving:.2f}."
        )

    # 4. Review: only flag if convergence not yet working (high run count + high per-run cost)
    if r["run_count"] >= _REVIEW_RUN_WARN:
        per_run = r["cost_usd_total"] / r["run_count"]
        if per_run >= 0.50:
            recs.append(
                f"Batch pushes or keep the PR in Draft until it's ready — each push "
                f"re-runs the full review. Convergence policy (inline = blocking only, "
                f"delta re-review from PR #13) should already be cutting $/run; "
                f"if per-run cost remains high, tighten the review rubric scope."
            )

    # 5. Turn cap hits
    maxturn = [x for x in r["runs"] if (x.get("result") or "") == "error_max_turns"]
    if maxturn:
        wasted = sum(float(x.get("cost_usd") or 0) for x in maxturn)
        recs.append(
            f"{len(maxturn)} review run(s) hit the turn cap (error_max_turns), "
            f"spending ${wasted:.2f} without finishing. "
            f"Raise --max-turns in claude-review.yml or tighten the rubric so it "
            f"converges inside the cap."
        )

    if not recs:
        recs.append("No obvious efficiency wins — costs look proportional to the change.")
    return recs


def _areas(rec: dict[str, Any]) -> list[dict[str, Any]]:
    """Rank cost contributors: each build session + review as one aggregate."""
    areas: list[dict[str, Any]] = []
    for s in rec["build"]["sessions"]:
        areas.append({
            "area": f"build session {s.get('ts')}",
            "model": s.get("model"),
            "tokens": int(s.get("tokens") or 0),
            "cost_usd": float(s.get("cost_usd") or 0),
        })
    r = rec["review"]
    if r["runs"]:
        areas.append({
            "area": f"claude review (×{r['run_count']} runs)",
            "model": r["runs"][0].get("model"),
            "tokens": r["tokens_total"],
            "cost_usd": r["cost_usd_total"],
        })
    areas.sort(key=lambda a: a["cost_usd"], reverse=True)
    return areas


def analyze(prs: list[int], top_n: int = 5) -> dict[str, Any]:
    records = [load_record(p) for p in prs]
    out_lines: list[str] = []
    reports = []
    for rec in records:
        _recompute_totals(rec)
        areas = _areas(rec)
        report = {
            "pr_number": rec["pr_number"],
            "title": rec.get("title"),
            "totals": rec["totals"],
            "build_cost_usd": rec["build"]["cost_usd_total"],
            "review_cost_usd": rec["review"]["cost_usd_total"],
            "top_areas": areas[:top_n],
            "observations": _observations(rec),
            "recommendations": _recommendations(rec),
        }
        reports.append(report)

        t = rec["totals"]
        out_lines.append(f"\n=== PR #{rec['pr_number']}: {rec.get('title') or '(untitled)'} ===")
        out_lines.append(
            f"  total: ${t['cost_usd']:.2f}  ({t['tokens']:,} tokens)   "
            f"build ${rec['build']['cost_usd_total']:.2f} | review ${rec['review']['cost_usd_total']:.2f}"
        )
        out_lines.append(f"  top {min(top_n, len(areas))} cost areas:")
        for i, a in enumerate(areas[:top_n], 1):
            share = (a["cost_usd"] / t["cost_usd"] * 100) if t["cost_usd"] else 0
            out_lines.append(
                f"    {i}. ${a['cost_usd']:>7.2f} ({share:4.1f}%)  {a['tokens']:>12,} tok  "
                f"{a['area']}  [{a['model']}]"
            )
        out_lines.append("  observations:")
        for ob in report["observations"]:
            out_lines.append(f"    • {ob}")
        out_lines.append("  recommendations (tactical):")
        for rc in report["recommendations"]:
            out_lines.append(f"    → {rc}")
    return {"reports": reports, "text": "\n".join(out_lines)}


# ── HTML report (checked-in, opens any/all PR ledgers) ──────────────

_REPORT_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin: 0; font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: #0d1117; color: #e6edf3; }
.wrap { max-width: 960px; margin: 0 auto; padding: 32px 24px 64px; }
h1 { font-size: 24px; margin: 0 0 4px; }
h2 { font-size: 15px; margin: 24px 0 8px; color: #adbac7; font-weight: 600; }
.muted { color: #768390; font-size: 12px; }
.pr { border: 1px solid #21262d; border-radius: 10px; padding: 20px; margin: 24px 0; background: #11151b; }
.pr-title { font-size: 17px; font-weight: 600; margin: 0 0 2px; }
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin: 14px 0; }
.stat { border: 1px solid #21262d; border-radius: 8px; padding: 12px; }
.stat .v { font-size: 22px; font-weight: 600; }
.stat .l { color: #768390; font-size: 12px; margin-top: 2px; }
.v.build { color: #d29922; } .v.review { color: #539bf5; }
.bar { display: flex; height: 14px; border-radius: 7px; overflow: hidden; margin: 6px 0 2px; }
.bar .b { background: #bb8009; } .bar .r { background: #316dca; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid #21262d; }
th { color: #768390; font-weight: 600; } td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; }
tr.build td:first-child { border-left: 3px solid #bb8009; } tr.review td:first-child { border-left: 3px solid #316dca; }
.obs { border: 1px solid #30363d; border-left: 3px solid #444c56; border-radius: 6px; padding: 10px 12px; margin: 8px 0;
  background: #0d1117; color: #adbac7; font-size: 13px; }
.obs b { color: #768390; margin-right: 6px; }
.rec { border: 1px solid #30363d; border-left: 3px solid #d29922; border-radius: 6px; padding: 10px 12px; margin: 8px 0;
  background: #15191f; }
.rec b { display: block; margin-bottom: 2px; color: #d29922; }
code { background: #21262d; padding: 1px 5px; border-radius: 4px; font-size: 12px; }
"""


def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


def _hhmm(ts: str) -> str:
    return ts[11:16] if ts and len(ts) >= 16 else _esc(ts)


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def _render_pr_html(rec: dict[str, Any]) -> str:
    _recompute_totals(rec)
    t = rec["totals"]
    total = t["cost_usd"] or 1.0
    b, r = rec["build"], rec["review"]
    build_pct = b["cost_usd_total"] / total * 100
    review_pct = r["cost_usd_total"] / total * 100
    areas = _areas(rec)[:5]

    area_rows = "".join(
        f'<tr class="{("review" if a["area"].startswith("claude review") else "build")}">'
        f"<td>{_esc(a['area'])}</td><td>{_esc(a['model'])}</td>"
        f'<td class="n">{_fmt_tokens(a["tokens"])}</td>'
        f'<td class="n">${a["cost_usd"]:.2f}</td>'
        f'<td class="n">{(a["cost_usd"] / total * 100):.1f}%</td></tr>'
        for a in areas
    )
    obs = _observations(rec)
    obs_html = "".join(
        f'<div class="obs"><b>•</b>{_esc(ob)}</div>'
        for ob in obs
    )
    recs = _recommendations(rec)
    rec_html = "".join(
        f'<div class="rec"><b>#{i + 1}</b>{_esc(rc)}</div>'
        for i, rc in enumerate(recs)
    )
    return f"""
    <section class="pr">
      <p class="pr-title">#{rec['pr_number']} — {_esc(rec.get('title') or '(untitled)')}</p>
      <p class="muted">merged {_esc((rec.get('merged_at') or '')[:10])} · build = Cursor usage API · review = Claude Sonnet bot</p>
      <div class="stats">
        <div class="stat"><div class="v">${t['cost_usd']:.2f}</div><div class="l">Total cost</div></div>
        <div class="stat"><div class="v build">${b['cost_usd_total']:.2f}</div><div class="l">Build ({build_pct:.0f}%)</div></div>
        <div class="stat"><div class="v review">${r['cost_usd_total']:.2f}</div><div class="l">Review · {r['run_count']} runs</div></div>
        <div class="stat"><div class="v">{_fmt_tokens(t['tokens'])}</div><div class="l">Tokens</div></div>
      </div>
      <div class="bar"><div class="b" style="width:{build_pct:.1f}%"></div><div class="r" style="width:{review_pct:.1f}%"></div></div>
      <p class="muted">Build {build_pct:.0f}% · Review {review_pct:.0f}%</p>
      <h2>Where the effort went — top {len(areas)} cost areas</h2>
      <table><thead><tr><th>Area</th><th>Model</th><th class="n">Tokens</th><th class="n">Cost</th><th class="n">% of PR</th></tr></thead>
      <tbody>{area_rows}</tbody></table>
      <h2>Diagnosis</h2>
      {obs_html}
      <h2>Tactical recommendations</h2>
      {rec_html}
    </section>
    """


def _render_report_html(records: list[dict[str, Any]]) -> str:
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    grand = sum((rec["totals"]["cost_usd"] or 0) for rec in records)
    body = "".join(_render_pr_html(rec) for rec in records)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PR cost monitoring</title>
<style>{_REPORT_CSS}</style></head>
<body><div class="wrap">
<h1>PR cost monitoring</h1>
<p class="muted">{len(records)} PR(s) · ${grand:.2f} total · generated {now} · source: <code>metrics/pr_cost/PR-*.json</code> · rates verified {_RATES_DATE} (<a href="https://cursor.com/docs/models-and-pricing" style="color:#539bf5">cursor.com/docs/models-and-pricing</a>)</p>
{body}
</div></body></html>
"""


def build_html_report(prs: list[int]) -> str:
    return _render_report_html([load_record(p) for p in prs])


# ── sync (the one pre-push step: capture build + review + report) ───

def sync(pr: int, *, repo: str | None = None, report_out: str | None = None) -> dict[str, Any]:
    """Refresh a PR's full cost record + the HTML report — best-effort per surface.

    The single thing to run before your final push so the commit carries the
    cost-so-far: pulls BUILD cost (Cursor usage API, auto-window) and REVIEW cost
    (posted PR comments), then regenerates metrics/pr_cost/report.html. A surface
    that can't be captured yet (e.g. no PR comments, no scored commits on a brand
    new branch) is warned and skipped, not fatal. The only cost it can never hold
    is the review run triggered by this very push — captured later by
    pr-cost-finalize.yml at merge.
    """
    try:
        rec0 = load_record(pr)
        capture_build(
            pr,
            conversation_auto=bool(rec0.get("session_started_at") or rec0["build"].get("conversation_ids")),
        )
    except (SystemExit, Exception) as exc:  # noqa: BLE001 — best-effort surface
        print(f"  [build] skipped: {exc}")
    try:
        capture_review(pr, repo=repo)
    except (SystemExit, Exception) as exc:  # noqa: BLE001 — best-effort surface
        print(f"  [review] skipped: {exc}")
    out = Path(report_out) if report_out else (LEDGER_DIR / "report.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    prs = _all_prs() or [pr]
    out.write_text(build_html_report(sorted(prs, reverse=True)), encoding="utf-8")
    rec = load_record(pr)
    print(f"  report → {out}")
    print(f"  PR #{pr}: build ${rec['build']['cost_usd_total']:.2f} | "
          f"review ${rec['review']['cost_usd_total']:.2f} | total ${rec['totals']['cost_usd']:.2f}")
    return rec


# ── CLI ─────────────────────────────────────────────────────────────

def _all_prs() -> list[int]:
    prs = []
    for p in glob.glob(str(LEDGER_DIR / "PR-*.json")):
        try:
            prs.append(int(Path(p).stem.split("-", 1)[1]))
        except (ValueError, IndexError):
            continue
    return sorted(prs)


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = cli.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("set-meta", help="Set PR metadata")
    m.add_argument("--pr", type=int, required=True)
    m.add_argument("--title"); m.add_argument("--requirement"); m.add_argument("--branch")
    m.add_argument("--created"); m.add_argument("--merged")
    m.add_argument("--files", type=int); m.add_argument("--additions", type=int); m.add_argument("--deletions", type=int)

    b = sub.add_parser("record-build", help="Add a Cursor build session row (manual)")
    b.add_argument("--pr", type=int, required=True)
    b.add_argument("--ts", required=True); b.add_argument("--tokens", type=int, required=True)
    b.add_argument("--cost", type=float, required=True); b.add_argument("--model", required=True)
    b.add_argument("--source", default="cursor_dashboard"); b.add_argument("--note")

    m.add_argument("--session-started", help="ISO8601 — when this PR's chat session began")
    m.add_argument("--conversation-id", action="append", dest="conversation_ids",
                   help="Cursor chat UUID (repeatable); from agent-transcripts/<uuid>/")

    bc = sub.add_parser("bind-conversation", help="Bind Cursor chat space(s) to a PR")
    bc.add_argument("--pr", type=int, required=True)
    bc.add_argument("--conversation-id", action="append", dest="conversation_ids")
    bc.add_argument("--auto", action="store_true",
                    help="Auto-pick conversationId(s) from ai_code_hashes since session_started_at")
    bc.add_argument("--session-started", help="ISO8601 session start (also stored on the record)")

    c = sub.add_parser("capture-build", help="Auto-fill build sessions from the Cursor usage API")
    c.add_argument("--pr", type=int, required=True)
    c.add_argument("--start", help="window start (ISO8601 or epoch-ms); omit to auto-derive from branch")
    c.add_argument("--end", help="window end (ISO8601 or epoch-ms); omit to auto-derive from branch")
    c.add_argument("--model-filter", help="only include events whose model contains this substring")
    c.add_argument("--conversation-auto", action="store_true",
                   help="auto-bind conversationId(s) from session_started_at before capture")
    c.add_argument("--allow-wide-manual", action="store_true",
                   help="allow manual windows >4h (marks approximate; parallel-chat bleed risk)")

    cr = sub.add_parser("capture-review", help="Reconstruct review runs from the PR's posted cost comments")
    cr.add_argument("--pr", type=int, required=True)
    cr.add_argument("--repo", help="owner/name (defaults to the current gh repo)")

    v = sub.add_parser("validate", help="Pre-merge gate: ensure costs are accounted")
    v.add_argument("--pr", type=int, required=True)
    v.add_argument("--require-build", action="store_true",
                   help="Hard gate: also require build sessions to be recorded.")

    a = sub.add_parser("analyze", help="Post-merge: top cost areas + recommendations")
    a.add_argument("--pr", type=int)
    a.add_argument("--top", type=int, default=5)
    a.add_argument("--json", action="store_true")

    rp = sub.add_parser("report", help="Render a standalone HTML cost report (any/all PRs)")
    rp.add_argument("--pr", type=int, help="single PR; omit for all recorded PRs")
    rp.add_argument("--out", default=str(LEDGER_DIR / "report.html"),
                    help="output HTML path (default: metrics/pr_cost/report.html)")

    sy = sub.add_parser("sync", help="Pre-push: capture build + review cost and regenerate the report")
    sy.add_argument("--pr", type=int, required=True)
    sy.add_argument("--repo", help="owner/name (defaults to the current gh repo)")
    sy.add_argument("--out", help="report output path (default: metrics/pr_cost/report.html)")

    bp = sub.add_parser(
        "bind-pr",
        help="Promote a provisional branch-keyed session to PR-<n>.json once the "
             "PR is actually opened (the number isn't known before `gh pr create`)",
    )
    bp.add_argument("--branch", required=True,
                    help="The session's branch (its provisional key)")
    bp.add_argument("--pr", type=int,
                    help="Real PR number; omit to auto-resolve from the branch via gh")
    bp.add_argument("--repo", help="owner/name (defaults to the current gh repo)")

    args = cli.parse_args(argv)

    if args.cmd == "bind-pr":
        bind_pr(args.branch, pr_number=args.pr, branch=args.branch, repo=args.repo)
        return 0

    if args.cmd == "set-meta":
        rec = set_meta(args.pr, title=args.title, requirement=args.requirement,
                       branch=args.branch, created_at=args.created, merged_at=args.merged,
                       files=args.files, additions=args.additions, deletions=args.deletions,
                       session_started_at=getattr(args, "session_started", None),
                       conversation_ids=getattr(args, "conversation_ids", None))
        print(f"updated {_record_path(args.pr)}")
        return 0
    if args.cmd == "bind-conversation":
        rec = bind_conversations(
            args.pr,
            conversation_ids=args.conversation_ids,
            auto=args.auto,
            session_started_at=args.session_started,
        )
        ids = rec["build"].get("conversation_ids") or []
        print(f"bound {len(ids)} conversation(s) on {_record_path(args.pr)}: {ids}")
        return 0
    if args.cmd == "record-build":
        record_build_session(args.pr, ts=args.ts, tokens=args.tokens, cost_usd=args.cost,
                             model=args.model, source=args.source, note=args.note)
        print(f"recorded build session {args.ts} on {_record_path(args.pr)}")
        return 0
    if args.cmd == "capture-build":
        rec = capture_build(
            args.pr, start=args.start, end=args.end, model_filter=args.model_filter,
            conversation_auto=args.conversation_auto,
            allow_wide_manual=args.allow_wide_manual,
        )
        n = len(rec["build"]["sessions"])
        w = rec["build"].get("window") or {}
        mode = rec["build"].get("attribution_mode") or "unknown"
        print(f"captured {n} build session(s) from Cursor usage API [{mode}]")
        print(f"  window: {w.get('start')} → {w.get('end')}")
        print(f"  build total ${rec['build']['cost_usd_total']:.2f} → {_record_path(args.pr)}")
        return 0
    if args.cmd == "capture-review":
        rec = capture_review(args.pr, repo=args.repo)
        r = rec["review"]
        print(f"captured {r['run_count']} review run(s) from PR cost comments "
              f"(review total ${r['cost_usd_total']:.2f}) → {_record_path(args.pr)}")
        return 0
    if args.cmd == "validate":
        ok, problems = validate(args.pr, require_build=args.require_build)
        if ok:
            print(f"[OK] PR #{args.pr} cost record is present and accounted.")
            return 0
        print(f"[FAIL] PR #{args.pr} cost record incomplete:")
        for p in problems:
            print(f"  - {p}")
        return 1
    if args.cmd == "analyze":
        prs = [args.pr] if args.pr else _all_prs()
        if not prs:
            print("no PR cost records found in metrics/pr_cost/")
            return 0
        result = analyze(prs, top_n=args.top)
        if args.json:
            print(json.dumps(result["reports"], indent=2))
        else:
            print(result["text"])
        return 0
    if args.cmd == "report":
        prs = [args.pr] if args.pr else _all_prs()
        if not prs:
            print("no PR cost records found in metrics/pr_cost/")
            return 0
        # Newest PR first so the most recent change leads the report.
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(build_html_report(sorted(prs, reverse=True)), encoding="utf-8")
        print(f"wrote HTML cost report for {len(prs)} PR(s) → {out}")
        return 0
    if args.cmd == "sync":
        print(f"syncing cost for PR #{args.pr} …")
        sync(args.pr, repo=args.repo, report_out=args.out)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
