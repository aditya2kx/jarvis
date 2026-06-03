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
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

STATE_DB = Path.home() / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"  # macOS only
AI_TRACKING_DB = Path.home() / ".cursor/ai-tracking/ai-code-tracking.db"
ENDPOINT = "https://cursor.com/api/dashboard/get-filtered-usage-events"


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
    (merged_at, or last commit + pre_buffer), then tighten to the min/max
    timestamp of AI edits in that range (usage events that produced code).
    The lead pad accounts for a request starting slightly before its edits land.
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
        if merged_at:
            hi = datetime.datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
        else:
            hi = c_end + datetime.timedelta(minutes=pre_buffer_min)
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


def fetch_usage_events(
    start: str | int, end: str | int, *,
    page_size: int = 200, max_pages: int = 25,
    state_db: Path = STATE_DB,
) -> list[dict[str, Any]]:
    """Return normalized per-request usage events in [start, end] (inclusive-ish).

    Each item: {ts_iso, ts_ms, model, input_tokens, output_tokens, cache_read,
    cache_write, tokens, cost_usd, is_headless, kind}.
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
            key = (ts_ms, e.get("model"), e.get("chargedCents"))
            if key in seen:
                continue
            seen.add(key)
            inp = int(tu.get("inputTokens", 0)); outp = int(tu.get("outputTokens", 0))
            cr = int(tu.get("cacheReadTokens", 0)); cw = int(tu.get("cacheWriteTokens", 0))
            out.append({
                "ts_ms": ts_ms,
                "ts_iso": datetime.datetime.fromtimestamp(ts_ms / 1000, datetime.timezone.utc).isoformat(),
                "model": e.get("model"),
                "input_tokens": inp, "output_tokens": outp,
                "cache_read": cr, "cache_write": cw,
                "tokens": inp + outp + cr + cw,
                "cost_usd": round((e.get("chargedCents") or 0) / 100, 4),
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
    args = cli.parse_args(argv)

    if args.cmd == "events":
        events = fetch_usage_events(args.start, args.end)
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
