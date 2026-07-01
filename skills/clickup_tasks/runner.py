"""ClickUp Tasks/Forms REST client.

Provides headless access to ClickUp task lists and individual tasks via the
v2 REST API authenticated with a PAT.  Designed for use both locally (PAT from
macOS Keychain) and in Cloud Run (PAT from a secret-backed env var), mirroring
the pattern in skills/clickup_chat/runner.py.

Endpoints used (all under https://api.clickup.com):
    GET /api/v2/list/{list_id}/task   -- paginate all tasks in a list
    GET /api/v2/task/{task_id}        -- single-task fetch (with custom fields)

Rate limiting: ClickUp's published limit is 100 req/min on PAT auth. A small
inter-page sleep keeps us safely under that limit.

Typical usage:
    from skills.clickup_tasks.runner import list_tasks, get_task

    tasks = list_tasks("901711373136", since_ts_ms=last_hw_epoch_ms)
    for t in tasks:
        print(t["name"], t.get("custom_fields"))
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
PAT_ENV_VAR = "CLICKUP_PAT"
DEFAULT_TEAM_ID = "9017956545"
LIST_CLOSING = "901711373136"  # ClickUp "Closing" list for Austin Palmetto

PAGE_LIMIT = 100
INTER_PAGE_SLEEP_S = 0.7  # ~85 req/min


# ---------------------------------------------------------------------------
# Authentication — identical pattern to skills/clickup_chat/runner.py
# ---------------------------------------------------------------------------

def _is_cloud_run() -> bool:
    return (
        os.environ.get("BHAGA_SECRETS_BACKEND", "").lower() == "gcp"
        or "K_SERVICE" in os.environ
        or "CLOUD_RUN_JOB" in os.environ
    )


def _validate_pat(pat: str, *, source: str) -> str:
    if not pat.startswith("pk_"):
        raise RuntimeError(
            f"ClickUp PAT from {source} does not look like a ClickUp PAT "
            f"(should start with 'pk_'). Got: {pat[:8]}..."
        )
    return pat


def get_pat() -> str:
    """Return the ClickUp PAT.

    Resolution order:
      1. CLICKUP_PAT env var (Cloud Run / CI, secret-backed).
      2. macOS Keychain service 'jarvis-clickup-palmetto-pat'.

    If the PAT is missing, the error message includes the exact fix command:
        python3 -m skills.credentials.registry hydrate jarvis-clickup-palmetto-pat
    """
    env_pat = os.environ.get(PAT_ENV_VAR, "").strip()
    if env_pat:
        return _validate_pat(env_pat, source=f"${PAT_ENV_VAR}")

    if _is_cloud_run():
        raise RuntimeError(
            f"ClickUp PAT not found in env var {PAT_ENV_VAR} while running in "
            f"Cloud Run. Wire the PAT as a secret-backed env var on the job: "
            f"--update-secrets {PAT_ENV_VAR}=jarvis-clickup-palmetto-pat:latest"
        )

    result = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ClickUp PAT not in Keychain (service={KEYCHAIN_SERVICE}). "
            f"Fix: python3 -m skills.credentials.registry hydrate jarvis-clickup-palmetto-pat"
        )
    return _validate_pat(result.stdout.strip(), source=f"Keychain {KEYCHAIN_SERVICE}")


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

_RETRYABLE_CODES = frozenset({429, 500, 502, 503, 504})
_MAX_RETRIES = 3
_INITIAL_BACKOFF_S = 2.0


def _request(path: str, *, pat: str | None = None) -> dict:
    """GET a ClickUp endpoint with retry/backoff for transient errors."""
    if pat is None:
        pat = get_pat()
    url = f"{API_BASE}{path}"

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        req = urllib.request.Request(url, headers={
            "Authorization": pat,
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:500]
            if e.code in _RETRYABLE_CODES and attempt < _MAX_RETRIES:
                wait = _INITIAL_BACKOFF_S * (2 ** attempt)
                print(
                    f"[clickup_tasks] {e.code} on GET {path} "
                    f"(attempt {attempt + 1}/{_MAX_RETRIES + 1}), retrying in {wait:.1f}s...",
                    file=sys.stderr,
                )
                time.sleep(wait)
                last_exc = e
                continue
            if e.code == 401:
                raise RuntimeError(
                    f"ClickUp PAT rejected (401). PAT in Keychain ({KEYCHAIN_SERVICE}) "
                    f"may be expired. Re-hydrate: "
                    f"python3 -m skills.credentials.registry hydrate jarvis-clickup-palmetto-pat"
                ) from e
            raise RuntimeError(
                f"ClickUp API error {e.code} on GET {path}: {body}"
            ) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < _MAX_RETRIES:
                wait = _INITIAL_BACKOFF_S * (2 ** attempt)
                print(
                    f"[clickup_tasks] Network error on GET {path} "
                    f"(attempt {attempt + 1}/{_MAX_RETRIES + 1}): {e}, "
                    f"retrying in {wait:.1f}s...",
                    file=sys.stderr,
                )
                time.sleep(wait)
                last_exc = e
                continue
            raise RuntimeError(
                f"ClickUp network error after {_MAX_RETRIES + 1} attempts on GET {path}: {e}"
            ) from e

    raise RuntimeError(
        f"ClickUp request failed after {_MAX_RETRIES + 1} attempts: {last_exc}"
    )


# ---------------------------------------------------------------------------
# Task API
# ---------------------------------------------------------------------------

def list_tasks(
    list_id: str,
    *,
    since_ts_ms: int | None = None,
    include_closed: bool = True,
    max_pages: int = 200,
    pat: str | None = None,
) -> list[dict]:
    """Fetch all tasks from a ClickUp list, optionally filtered by creation time.

    Args:
        list_id: ClickUp list id (e.g. LIST_CLOSING = "901711373136").
        since_ts_ms: If provided, only return tasks whose date_created (epoch ms)
            is strictly greater than this value.  Use for incremental fetches.
        include_closed: Include tasks with status "closed" (default True — closing
            form submissions typically end up closed after daily processing).
        max_pages: Hard pagination cap (safety net for large backfills).
        pat: Caller-supplied PAT; loaded from Keychain/env if omitted.

    Returns:
        Tasks in ASCENDING date_created order (oldest first) so incremental
        high-water-mark logic can process them as a monotone stream.

    Note: Custom fields are included in the response payload. The ClickUp v2
    List Tasks endpoint returns ``custom_fields`` on each task object.
    """
    if pat is None:
        pat = get_pat()

    all_tasks: list[dict] = []
    page = 0

    while page < max_pages:
        params: dict[str, str] = {
            "page": str(page),
            "order_by": "created",
            "reverse": "false",
            "include_closed": "true" if include_closed else "false",
            "subtasks": "false",
        }
        if since_ts_ms is not None:
            # ClickUp date_created_gt filters tasks created after this epoch-ms value.
            params["date_created_gt"] = str(since_ts_ms)

        qs = urllib.parse.urlencode(params)
        data = _request(f"/api/v2/list/{list_id}/task?{qs}", pat=pat)

        page_tasks = data.get("tasks", [])
        if not page_tasks:
            break

        all_tasks.extend(page_tasks)
        page += 1

        if data.get("last_page", False):
            break

        time.sleep(INTER_PAGE_SLEEP_S)

    return all_tasks


def get_task(task_id: str, *, pat: str | None = None) -> dict:
    """Fetch a single ClickUp task by id, including custom fields.

    Args:
        task_id: The ClickUp task id (e.g. "86e0m3yy8").
        pat: Caller-supplied PAT; loaded from Keychain/env if omitted.
    """
    return _request(f"/api/v2/task/{task_id}", pat=pat)


# ---------------------------------------------------------------------------
# CLI for ad-hoc exploration / smoke tests
# ---------------------------------------------------------------------------

def _cli() -> int:
    import argparse
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest="action", required=True)

    sub.add_parser("ping", help="Smoke-test the PAT.")

    p_list = sub.add_parser("list", help="List tasks in a ClickUp list.")
    p_list.add_argument("--list-id", default=LIST_CLOSING)
    p_list.add_argument("--limit", type=int, default=5,
                        help="Max tasks to display (default 5)")
    p_list.add_argument("--max-pages", type=int, default=2,
                        help="Max API pages to fetch (default 2; use higher for backfill)")
    p_list.add_argument("--since-ts-ms", type=int, default=None)

    p_get = sub.add_parser("get", help="Fetch a single task.")
    p_get.add_argument("task_id")

    args = cli.parse_args()
    pat = get_pat()

    if args.action == "ping":
        data = _request("/api/v2/team", pat=pat)
        teams = data.get("teams", [])
        print(f"PAT OK. {len(teams)} team(s):")
        for t in teams:
            print(f"  team_id={t['id']}  name={t['name']}")
        return 0

    if args.action == "list":
        tasks = list_tasks(args.list_id, since_ts_ms=args.since_ts_ms,
                           max_pages=args.max_pages, pat=pat)
        shown = tasks[:args.limit]
        for t in shown:
            cf_count = len(t.get("custom_fields") or [])
            print(f"  id={t.get('id')}  name={t.get('name')!r}  custom_fields={cf_count}")
        print(f"\nShowing {len(shown)} of {len(tasks)} task(s) fetched.")
        return 0

    if args.action == "get":
        t = get_task(args.task_id, pat=pat)
        if "err" in t:
            print(f"Error: {t}", file=sys.stderr)
            return 1
        cf = t.get("custom_fields") or []
        print(f"id={t.get('id')}  name={t.get('name')!r}")
        print(f"custom_fields ({len(cf)}):")
        for f in cf:
            v = f.get("value")
            if v not in (None, "", []):
                print(f"  [{f.get('id')}] {f.get('name')!r} = {v!r}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
