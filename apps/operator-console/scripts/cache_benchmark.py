#!/usr/bin/env python3
"""Time every console page with the query cache off vs on, then check cached == live.

    python3 apps/operator-console/scripts/cache_benchmark.py --branch preview/console-cache
    python3 apps/operator-console/scripts/cache_benchmark.py --pr 420 --runs 5

Previews only (authenticates through IAP as the Cursor Cloud Agent SA, like
scripts/console_preview.py). Per page: `x-console-cache: off` renders (every read hits
BigQuery), one normal render to fill the cache, cached renders, then one
`x-console-cache: verify` render that re-runs every read live and compares it with the
cached copy. Prints a Markdown table of timings plus the parity counts from
/api/cache-stats. Logs timings only — never page content (Payroll/Accounting carry PII).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import cloud_agent_gcp as G  # noqa: E402
import console_preview as P  # noqa: E402

PAGES = ["/home", "/sales", "/labor", "/order-quality", "/payroll", "/inventory", "/accounting", "/automations", "/pipeline"]


def fetch(url: str, token: str, mode: str | None = None) -> tuple[int, float]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    if mode:
        req.add_header("x-console-cache", mode)
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    return status, time.perf_counter() - t


def wait_for_iap(base: str, token: str) -> None:
    for _ in range(P.IAP_GRANT_RETRIES):
        status, _ = fetch(base + "/api/cache-stats", token)
        if status != 403:
            return
        time.sleep(15)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = ap.add_mutually_exclusive_group(required=True)
    target.add_argument("--pr", type=int)
    target.add_argument("--branch")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("paths", nargs="*", default=PAGES)
    a = ap.parse_args(argv)

    service = P.pr_service(a.pr) if a.pr is not None else P.branch_service(a.branch)
    base = P.URL.format(service=service)
    token = G.sa_id_token(G.federated_access_token(G.cursor_oidc_token()))
    wait_for_iap(base, token)
    fetch(base + "/home", token)  # wake a scale-to-zero instance before timing anything

    rows = []
    for path in a.paths:
        url = base + path
        off = [fetch(url, token, "off") for _ in range(a.runs)]
        fill_status, fill = fetch(url, token)
        on = [fetch(url, token) for _ in range(a.runs)]
        verify_status, _ = fetch(url, token, "verify")
        statuses = {s for s, _ in off + on} | {fill_status, verify_status}
        rows.append(
            (path, statistics.median(t for _, t in off), fill, statistics.median(t for _, t in on), statuses)
        )
        print(f"bench {path}: off={rows[-1][1]:.2f}s first={fill:.2f}s cached={rows[-1][3]:.2f}s status={sorted(statuses)}", file=sys.stderr)

    req = urllib.request.Request(base + "/api/cache-stats", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        stats = json.load(r)
    counts = stats.get("counts", {})

    print(f"| Page | Cache off (every read → BigQuery) | First load (fills cache) | Cached | Faster |")
    print("|---|---|---|---|---|")
    for path, off, fill, on, statuses in rows:
        flag = "" if statuses == {200} else f" (HTTP {sorted(statuses)})"
        print(f"| `{path}`{flag} | {off:.2f} s | {fill:.2f} s | {on:.2f} s | {off / on:.1f}× |")
    print()
    print(
        "Parity (verify renders, cached vs live BigQuery): "
        f"{counts.get('verify-match', 0)} reads identical, {counts.get('verify-diff', 0)} different, "
        f"{counts.get('verify-cold', 0)} not cached at the time."
    )
    skipped = {k: v for k, v in counts.items() if k in ("uncacheable", "bypass", "error")}
    print(f"Not cacheable / bypassed / errors: {skipped or 'none'}")
    reasons = sorted({e.get("reason") for e in stats.get("recent", []) if e.get("outcome") in ("uncacheable", "error", "verify-diff")} - {None})
    for r in reasons:
        print(f"- {r}")
    print(f"L1: {stats.get('l1')}  catalog: {stats.get('catalog')}  L2: {stats.get('l2')}")
    return 0 if counts.get("verify-diff", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
