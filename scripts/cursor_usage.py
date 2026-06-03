#!/usr/bin/env python3
"""Pull per-request Cursor usage (tokens + cost + model) for an INDIVIDUAL account.

Cursor's documented per-request usage feed (`/teams/filtered-usage-events`) needs
a team/Enterprise Admin key. But the dashboard's own endpoint
`https://cursor.com/api/dashboard/get-filtered-usage-events` returns the exact
same data for a personal account when called with the local Cursor session token
(the one the desktop app already stored). This module reads that token from the
app's state DB and calls the endpoint — no Playwright, no manual entry.

Each event carries: timestamp, model, tokenUsage{input,output,cacheWrite,cacheRead},
chargedCents (authoritative billed cost), isHeadless.

It is an UNDOCUMENTED endpoint (the supported path is the team Admin API), so treat
it as best-effort: if Cursor changes it, fall back to manual `record-build`.

Security: the session token is read at runtime from the local app DB and used only
as the request cookie. It is never printed, logged, or written to the ledger.

CLI:
    python3 scripts/cursor_usage.py events \\
        --start 2026-06-02T17:50:00Z --end 2026-06-02T19:30:00Z
"""

from __future__ import annotations

import argparse
import base64
import datetime
import json
import sqlite3
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

STATE_DB = Path.home() / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"  # macOS only
AI_TRACKING_DB = Path.home() / ".cursor/ai-tracking/ai-code-tracking.db"
ENDPOINT = "https://cursor.com/api/dashboard/get-filtered-usage-events"
# Usage events have no conversationId — attribution uses edit windows from ai_code_hashes.
_CONVERSATION_EVENT_PAD_MS = 5 * 60_000  # allow usage slightly before first edit
_MAX_MANUAL_WINDOW_MS = 4 * 3600 * 1000  # reject wide manual windows (parallel-chat bleed)


class CursorUsageError(RuntimeError):
    pass


def _read_access_token(state_db: Path = STATE_DB) -> str:
    if not state_db.is_file():
        raise CursorUsageError(f"Cursor state DB not found at {state_db}")
    con = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True)
    try:
        row = con.execute(
            "select value from ItemTable where key='cursorAuth/accessToken'"
        ).fetchone()
    finally:
        con.close()
    if not row or not row[0]:
        raise CursorUsageError("cursorAuth/accessToken not present — sign in to Cursor first")
    return row[0]


def _jwt_claims(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception as exc:  # noqa: BLE001 — any malformed token → one clear error
        raise CursorUsageError(f"could not decode session token as JWT: {exc}") from exc


def _session_cookie(token: str) -> str:
    claims = _jwt_claims(token)
    exp = claims.get("exp")
    if exp and datetime.datetime.now(datetime.timezone.utc).timestamp() > exp:
        raise CursorUsageError("Cursor session token is expired — reopen/sign in to Cursor")
    # cookie value is "<userId>::<jwt>"; userId is the sub minus the auth provider prefix
    user_id = str(claims.get("sub", "")).split("|")[-1]
    if not user_id:
        raise CursorUsageError("could not derive user id from session token")
    return f"WorkosCursorSessionToken={user_id}::{token}"


def to_ms(value: str | int) -> int:
    if isinstance(value, int):
        return value
    s = str(value).strip()
    if s.isdigit():
        return int(s)
    # ISO 8601; accept trailing Z
    dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1000)


def _parse_git_date(s: str) -> datetime.datetime:
    # git's default commitDate format, e.g. "Tue Jun 2 13:26:27 2026 -0500"
    return datetime.datetime.strptime(s.strip(), "%a %b %d %H:%M:%S %Y %z")


def derive_window_for_branch(
    branch: str, *, merged_at: str | None = None,
    pre_buffer_min: int = 120, lead_pad_min: int = 10, tail_pad_min: int = 5,
    db: Path = AI_TRACKING_DB,
) -> tuple[int, int]:
    """Derive a build time window (epoch ms) for a branch from Cursor's local
    ai-code-tracking.db — anchored to actual AI *code edits* on that branch.

    Strategy: bound a search range from (first scored commit − pre_buffer) to
    (merged_at, or now), then tighten to the min/max timestamp of AI edits in
    that range (usage events that produced code). The lead pad accounts for a
    request starting slightly before its edits land.

    Non-overlap guard: the lower bound is clamped so it can never reach before
    the most recent commit of ANY OTHER branch that predates this branch's first
    commit. Without this, the pre_buffer reaches back into a previous PR built in
    the same session and double-counts its (often expensive) build cost — the
    failure seen on PR #14, which folded in all of PR #13's sessions.
    Returns (start_ms, end_ms). Raises if the branch has no scored commits.
    """
    if not db.is_file():
        raise CursorUsageError(f"ai-code-tracking.db not found at {db}")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "select commitDate from scored_commits where branchName=?", (branch,)
        ).fetchall()
        if not rows:
            raise CursorUsageError(
                f"no scored commits for branch {branch!r} in ai-code-tracking.db — "
                f"pass --start/--end manually"
            )
        dates = [_parse_git_date(r[0]) for r in rows]
        c_start, c_end = min(dates), max(dates)
        lo = c_start - datetime.timedelta(minutes=pre_buffer_min)
        # Clamp to the last commit of prior work so we don't bleed into the
        # previous back-to-back PR/branch from the same session.
        others = con.execute(
            "select commitDate from scored_commits where branchName != ?", (branch,)
        ).fetchall()
        prior = [d for d in (_parse_git_date(o[0]) for o in others) if d < c_start]
        if prior:
            lo = max(lo, max(prior))
        if merged_at:
            hi = datetime.datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
        else:
            # Unmerged: cap at now, never a fixed buffer into the future (which
            # would sweep in later, unrelated activity).
            hi = min(c_end + datetime.timedelta(minutes=pre_buffer_min),
                     datetime.datetime.now(datetime.timezone.utc))
        lo_ms, hi_ms = int(lo.timestamp() * 1000), int(hi.timestamp() * 1000)
        edit = con.execute(
            "select min(timestamp), max(timestamp) from ai_code_hashes "
            "where timestamp between ? and ?", (lo_ms, hi_ms),
        ).fetchone()
    finally:
        con.close()
    if not edit or edit[0] is None:
        return lo_ms, hi_ms  # no edits found — fall back to the commit-bounded range
    start_ms = max(lo_ms, int(edit[0]) - lead_pad_min * 60_000)
    end_ms = min(hi_ms, int(edit[1]) + tail_pad_min * 60_000)
    return start_ms, end_ms


def git_branch_commit_range_ms(
    branch: str, *, base: str = "main", repo_root: Path | None = None,
) -> tuple[int, int]:
    """First/last commit time on branch vs base (fallback when scored_commits missing)."""
    cwd = repo_root or Path(__file__).resolve().parent.parent
    for ref in (f"origin/{base}..{branch}", f"{base}..{branch}"):
        try:
            lines = subprocess.check_output(
                ["git", "log", ref, "--format=%cI"],
                cwd=cwd, text=True, stderr=subprocess.DEVNULL,
            ).strip().splitlines()
        except subprocess.CalledProcessError:
            continue
        if lines:
            dates = [
                datetime.datetime.fromisoformat(ln.replace("Z", "+00:00"))
                for ln in lines if ln.strip()
            ]
            if dates:
                lo, hi = min(dates), max(dates)
                return int(lo.timestamp() * 1000), int(hi.timestamp() * 1000)
    raise CursorUsageError(f"no git commits for {branch!r} vs {base}")


def conversation_profiles(
    start_ms: int, end_ms: int, *, db: Path = AI_TRACKING_DB,
) -> dict[str, dict[str, Any]]:
    """Per-conversation edit activity in [start_ms, end_ms] from ai_code_hashes."""
    if not db.is_file():
        return {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "select conversationId, model, timestamp from ai_code_hashes "
            "where conversationId is not null and conversationId != '' "
            "and timestamp between ? and ?",
            (start_ms, end_ms),
        ).fetchall()
    finally:
        con.close()
    profiles: dict[str, dict[str, Any]] = {}
    for cid, model, ts in rows:
        p = profiles.setdefault(cid, {
            "conversation_id": cid,
            "min_ts": ts, "max_ts": ts,
            "edit_count": 0,
            "models": set(),
        })
        p["min_ts"] = min(p["min_ts"], ts)
        p["max_ts"] = max(p["max_ts"], ts)
        p["edit_count"] += 1
        if model:
            p["models"].add(model)
    for p in profiles.values():
        p["dominant_model"] = _dominant_model(p["models"])
        p["models"] = sorted(p["models"])
    return profiles


def _dominant_model(models: set[str]) -> str | None:
    if not models:
        return None
    # Prefer the most specific (longest) model string — usually the full name.
    return max(models, key=len)


def _model_tier(model: str | None) -> str:
    m = (model or "").lower()
    if "opus" in m:
        return "opus"
    if "sonnet" in m:
        return "sonnet"
    if "haiku" in m:
        return "haiku"
    if "composer" in m:
        return "composer"
    return "other"


def _models_compatible(event_model: str | None, conv_model: str | None) -> bool:
    """Usage event model must match the conversation's dominant model tier."""
    if not conv_model:
        return True
    return _model_tier(event_model) == _model_tier(conv_model)


def auto_bind_conversations(
    start_ms: int, end_ms: int, *, db: Path = AI_TRACKING_DB,
    min_edits: int = 1, min_share: float = 0.55,
) -> list[str]:
    """Pick conversationId(s) with enough edit activity in the window.

    Returns one id when a single conversation dominates edit share; otherwise
    returns all ids above min_edits (caller may bind explicitly).
    """
    profiles = conversation_profiles(start_ms, end_ms, db=db)
    ranked = sorted(
        profiles.values(), key=lambda p: p["edit_count"], reverse=True,
    )
    ranked = [p for p in ranked if p["edit_count"] >= min_edits]
    if not ranked:
        return []
    total_edits = sum(p["edit_count"] for p in ranked)
    if len(ranked) == 1 or ranked[0]["edit_count"] / total_edits >= min_share:
        return [ranked[0]["conversation_id"]]
    return [p["conversation_id"] for p in ranked]


def filter_events_for_conversations(
    events: list[dict[str, Any]],
    conversation_ids: list[str],
    start_ms: int,
    end_ms: int,
    *,
    db: Path = AI_TRACKING_DB,
) -> list[dict[str, Any]]:
    """Keep usage events attributable to the given chat space(s).

    The usage API is account-global and has no conversationId. We match each
    event to a bound conversation when its timestamp falls in that chat's edit
    window and its model tier matches the chat's dominant model.
    """
    if not conversation_ids:
        return events
    profiles = conversation_profiles(start_ms, end_ms, db=db)
    bound = {cid: profiles[cid] for cid in conversation_ids if cid in profiles}
    if not bound:
        # Bound ids may predate edits in window — widen profile lookup to full range.
        for cid in conversation_ids:
            if not db.is_file():
                continue
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                row = con.execute(
                    "select min(timestamp), max(timestamp), group_concat(distinct model) "
                    "from ai_code_hashes where conversationId=?",
                    (cid,),
                ).fetchone()
            finally:
                con.close()
            if row and row[0] is not None:
                models = set(filter(None, (row[2] or "").split(",")))
                bound[cid] = {
                    "conversation_id": cid,
                    "min_ts": row[0], "max_ts": row[1],
                    "edit_count": 0,
                    "models": sorted(models),
                    "dominant_model": _dominant_model(models),
                }
    out: list[dict[str, Any]] = []
    for e in events:
        ts = e["ts_ms"]
        if ts < start_ms or ts > end_ms:
            continue
        candidates: list[tuple[str, int]] = []
        for cid, prof in bound.items():
            lo = max(start_ms, prof["min_ts"] - _CONVERSATION_EVENT_PAD_MS)
            hi = min(end_ms, prof["max_ts"] + _CONVERSATION_EVENT_PAD_MS)
            if ts < lo or ts > hi:
                continue
            if not _models_compatible(e.get("model"), prof.get("dominant_model")):
                continue
            # Prefer the conversation whose edit window is closest to this event.
            dist = min(abs(ts - prof["min_ts"]), abs(ts - prof["max_ts"]))
            candidates.append((cid, dist))
        if not candidates:
            continue
        candidates.sort(key=lambda x: x[1])
        row = dict(e)
        row["conversation_id"] = candidates[0][0]
        out.append(row)
    return out


def manual_window_span_ms(start: str | int, end: str | int) -> int:
    return abs(to_ms(end) - to_ms(start))


def resolve_event_cost_cents(event: dict[str, Any]) -> tuple[float, str]:
    """Map a raw dashboard usage event to billed cents + source label.

    Cursor sets chargedCents=0 for BYOK Anthropic requests but still populates
    tokenUsage.totalCents (list-price model cost, ~what Anthropic bills). When
    chargedCents is absent/zero, fall back to totalCents + cursorTokenFee.
    """
    tu = event.get("tokenUsage") or {}
    charged = event.get("chargedCents")
    token_fee = float(event.get("cursorTokenFee") or 0)
    total = float(tu.get("totalCents") or 0)
    if charged is not None and float(charged) > 0:
        return float(charged), "cursor_charged"
    if total > 0 or token_fee > 0:
        return total + token_fee, "byok_token_usage"
    return 0.0, "zero"


def fetch_usage_events(
    start: str | int, end: str | int, *,
    page_size: int = 200, max_pages: int = 25,
    state_db: Path = STATE_DB,
) -> list[dict[str, Any]]:
    """Return normalized per-request usage events in [start, end] (inclusive-ish).

Each item: {ts_iso, ts_ms, model, input_tokens, output_tokens, cache_read,
    cache_write, tokens, cost_usd, cost_source, is_headless, kind}.

    cost_source:
      - cursor_charged — chargedCents from Cursor (subscription / Cursor-billed)
      - byok_token_usage — chargedCents is 0 but tokenUsage.totalCents (+ cursorTokenFee)
        is present; typical when an Anthropic API key is configured in Cursor (BYOK).
      - zero — no cost fields on the event
    """
    token = _read_access_token(state_db)
    cookie = _session_cookie(token)
    start_ms, end_ms = to_ms(start), to_ms(end)
    out: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for page in range(1, max_pages + 1):
        body = json.dumps({
            "teamId": 0, "startDate": str(start_ms), "endDate": str(end_ms),
            "page": page, "pageSize": page_size,
        }).encode()
        req = urllib.request.Request(
            ENDPOINT, data=body, method="POST",
            headers={
                "Content-Type": "application/json",
                "Origin": "https://cursor.com",
                "Referer": "https://cursor.com/dashboard",
                "Cookie": cookie,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise CursorUsageError(f"usage endpoint HTTP {exc.code}: {exc.read()[:200]!r}") from exc
        events = payload.get("usageEventsDisplay") or []
        if not events:
            break
        for e in events:
            tu = e.get("tokenUsage") or {}
            ts_ms = int(e["timestamp"])
            inp = int(tu.get("inputTokens", 0)); outp = int(tu.get("outputTokens", 0))
            cr = int(tu.get("cacheReadTokens", 0)); cw = int(tu.get("cacheWriteTokens", 0))
            # Dedup only removes true page-overlap repeats. Prefer a stable event
            # id when present; otherwise fingerprint the full content so two
            # distinct same-ms events (e.g. both with chargedCents=None) aren't
            # collapsed onto one another.
            key = e.get("requestId") or e.get("id") or (
                ts_ms, e.get("model"), e.get("chargedCents"), inp, outp, cr, cw,
            )
            if key in seen:
                continue
            seen.add(key)
            cost_cents, cost_source = resolve_event_cost_cents(e)
            out.append({
                "ts_ms": ts_ms,
                "ts_iso": datetime.datetime.fromtimestamp(ts_ms / 1000, datetime.timezone.utc).isoformat(),
                "model": e.get("model"),
                "input_tokens": inp, "output_tokens": outp,
                "cache_read": cr, "cache_write": cw,
                "tokens": inp + outp + cr + cw,
                "cost_usd": round(cost_cents / 100, 4),
                "cost_source": cost_source,
                "is_headless": bool(e.get("isHeadless")),
                "kind": e.get("kind"),
            })
        if len(events) < page_size:
            break
    out.sort(key=lambda x: x["ts_ms"])
    return out


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = cli.add_subparsers(dest="cmd", required=True)
    ev = sub.add_parser("events", help="List/sum usage events in a time window")
    ev.add_argument("--start", required=True, help="ISO8601 or epoch-ms")
    ev.add_argument("--end", required=True, help="ISO8601 or epoch-ms")
    ev.add_argument("--json", action="store_true")
    ev.add_argument("--state-db", default=None,
                    help="Override the Cursor state DB path (default: macOS ~/Library/.../state.vscdb)")
    args = cli.parse_args(argv)

    if args.cmd == "events":
        state_db = Path(args.state_db) if args.state_db else STATE_DB
        events = fetch_usage_events(args.start, args.end, state_db=state_db)
        if args.json:
            print(json.dumps(events, indent=2))
            return 0
        tot_c = sum(e["cost_usd"] for e in events)
        tot_t = sum(e["tokens"] for e in events)
        for e in events:
            ts = e["ts_iso"][11:19]
            print(f"  {ts}Z  {e['model']:36}  {e['tokens']:>12,} tok  ${e['cost_usd']:7.2f}"
                  + ("  [headless]" if e["is_headless"] else ""))
        print(f"--- {len(events)} requests  |  {tot_t:,} tokens  |  ${tot_c:.2f}")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
