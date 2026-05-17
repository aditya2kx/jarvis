"""ClickUp v3 Chat REST client.

Why this exists: ClickUp's task MCPs don't expose chat-message reads, but the
v3 chat REST endpoints do. This skill is a focused wrapper that:

  - Pulls the PAT from macOS Keychain (no secrets in code)
  - Lists channels in a workspace (team)
  - Resolves a channel name to its id
  - Paginates messages with cursor support
  - Supports incremental fetch via since_ts_ms (high-water mark idempotency)

Endpoints used (all under https://api.clickup.com):
    GET /api/v2/team
    GET /api/v3/workspaces/{team_id}/chat/channels?limit=100
    GET /api/v3/workspaces/{team_id}/chat/channels/{channel_id}/messages?limit=50&cursor=...

Rate limiting: ClickUp's published limit is 100 req/min on PAT auth. We add
a small inter-page sleep when paginating to stay safely under that.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterator

API_BASE = "https://api.clickup.com"
KEYCHAIN_SERVICE = "jarvis-clickup-palmetto-pat"
DEFAULT_TEAM_ID = "9017956545"  # Austin Mueller Palmetto
PAGE_LIMIT = 50
INTER_PAGE_SLEEP_S = 0.7  # ~85 req/min ceiling


def get_pat() -> str:
    """Read the ClickUp PAT from Keychain. Raises if missing."""
    result = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ClickUp PAT not in Keychain (service={KEYCHAIN_SERVICE}). "
            f"Re-run the credential setup: see skills/credentials/registry.py."
        )
    pat = result.stdout.strip()
    if not pat.startswith("pk_"):
        raise RuntimeError(
            f"Keychain entry for {KEYCHAIN_SERVICE} does not look like a "
            f"ClickUp PAT (should start with 'pk_'). Got: {pat[:8]}..."
        )
    return pat


def _request(path: str, *, pat: str | None = None) -> dict:
    """GET a ClickUp endpoint. Returns the parsed JSON body."""
    if pat is None:
        pat = get_pat()
    url = f"{API_BASE}{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": pat,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(
            f"ClickUp API error {e.code} on GET {path}: {body}"
        ) from e


def list_channels(team_id: str = DEFAULT_TEAM_ID, *, pat: str | None = None) -> list[dict]:
    """List ALL chat channels in a workspace (paginates automatically)."""
    out: list[dict] = []
    cursor: str | None = None
    while True:
        qs = f"limit=100"
        if cursor:
            qs += f"&cursor={urllib.parse.quote(cursor)}"
        data = _request(f"/api/v3/workspaces/{team_id}/chat/channels?{qs}", pat=pat)
        out.extend(data.get("data", []))
        cursor = data.get("next_cursor")
        if not cursor:
            break
        time.sleep(INTER_PAGE_SLEEP_S)
    return out


def find_channel_by_name(
    name: str, *, team_id: str = DEFAULT_TEAM_ID, pat: str | None = None
) -> dict | None:
    """Look up a single channel by exact name match. Returns None if absent."""
    # Strip a leading '#' if the caller passed e.g. "#running-austin-palmetto".
    needle = name.lstrip("#").strip().lower()
    for c in list_channels(team_id=team_id, pat=pat):
        ch_name = (c.get("name") or "").strip().lower()
        if ch_name == needle:
            return c
    return None


def fetch_messages(
    channel_id: str,
    *,
    team_id: str = DEFAULT_TEAM_ID,
    since_ts_ms: int | None = None,
    max_pages: int = 20,
    pat: str | None = None,
) -> list[dict]:
    """Fetch chat messages from a channel, optionally since a high-water timestamp.

    Args:
        channel_id: the chat channel id (e.g. "8cr6661-737").
        team_id: the ClickUp workspace/team id.
        since_ts_ms: if provided, stop paginating once we see messages older
            than this epoch-ms timestamp. The returned list includes ONLY
            messages with `date` strictly greater than since_ts_ms.
        max_pages: hard cap on pagination depth (safety net for backfills).
        pat: caller-provided PAT (else loaded from Keychain).

    Returns:
        Messages in CHRONOLOGICAL order (oldest first) so downstream
        idempotent state machines can process strictly increasing timestamps.
    """
    if pat is None:
        pat = get_pat()

    all_msgs: list[dict] = []
    cursor: str | None = None
    pages = 0
    stop_seen = False

    while pages < max_pages:
        pages += 1
        qs = f"limit={PAGE_LIMIT}"
        if cursor:
            qs += f"&cursor={urllib.parse.quote(cursor)}"
        path = f"/api/v3/workspaces/{team_id}/chat/channels/{channel_id}/messages?{qs}"
        data = _request(path, pat=pat)

        page_msgs = data.get("data", [])
        if not page_msgs:
            break

        for m in page_msgs:
            ts = m.get("date") or 0
            if since_ts_ms is not None and ts <= since_ts_ms:
                stop_seen = True
                continue
            all_msgs.append(m)

        if stop_seen:
            break

        cursor = data.get("next_cursor")
        if not cursor:
            break
        time.sleep(INTER_PAGE_SLEEP_S)

    # API returns newest-first; flip to chronological for downstream consumers.
    all_msgs.sort(key=lambda m: m.get("date") or 0)
    return all_msgs


# ── CLI for ad-hoc exploration / smoke tests ─────────────────────────


def _cli() -> int:
    import argparse
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest="action", required=True)

    sub.add_parser("ping", help="Smoke-test the PAT against v2 /team.")

    p_list = sub.add_parser("list-channels", help="List all chat channels.")
    p_list.add_argument("--team-id", default=DEFAULT_TEAM_ID)

    p_find = sub.add_parser("find-channel", help="Resolve a channel name to id.")
    p_find.add_argument("name")
    p_find.add_argument("--team-id", default=DEFAULT_TEAM_ID)

    p_msgs = sub.add_parser("messages", help="Fetch messages from a channel.")
    p_msgs.add_argument("channel_id")
    p_msgs.add_argument("--team-id", default=DEFAULT_TEAM_ID)
    p_msgs.add_argument("--since-ts-ms", type=int, default=None)
    p_msgs.add_argument("--max-pages", type=int, default=20)
    p_msgs.add_argument("--preview", action="store_true",
                        help="Print short preview lines instead of full JSON.")

    args = cli.parse_args()
    pat = get_pat()

    if args.action == "ping":
        data = _request("/api/v2/team", pat=pat)
        teams = data.get("teams", [])
        print(f"PAT OK. {len(teams)} team(s):")
        for t in teams:
            print(f"  team_id={t['id']}  name={t['name']}  members={len(t.get('members', []))}")
        return 0

    if args.action == "list-channels":
        chans = list_channels(team_id=args.team_id, pat=pat)
        for c in chans:
            print(f"  id={c.get('id',''):40s}  name={c.get('name','')}")
        print(f"\nTotal: {len(chans)}")
        return 0

    if args.action == "find-channel":
        c = find_channel_by_name(args.name, team_id=args.team_id, pat=pat)
        if c is None:
            print(f"No channel named {args.name!r}")
            return 1
        print(json.dumps(c, indent=2))
        return 0

    if args.action == "messages":
        msgs = fetch_messages(
            args.channel_id, team_id=args.team_id,
            since_ts_ms=args.since_ts_ms, max_pages=args.max_pages, pat=pat,
        )
        if args.preview:
            for m in msgs:
                preview = (m.get("content") or "")[:120].replace("\n", " | ")
                print(f"  [{m.get('date')}] id={m.get('id')}  {preview}")
            print(f"\nTotal: {len(msgs)} messages")
        else:
            print(json.dumps(msgs, indent=2, default=str))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
