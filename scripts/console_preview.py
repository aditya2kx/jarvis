#!/usr/bin/env python3
"""Open an Operator Console PR preview from a Cursor Cloud Agent VM — no Google sign-in.

    python3 scripts/console_preview.py --pr 385 /home /inventory --shots /tmp/shots [--mobile]

Authenticates to IAP with the cursor-cloud-agent ID token (scripts/cloud_agent_gcp.py id-token);
previews only — prod's IAP list does not include that identity. Prints one line per path
(status, final path, title); screenshots are local files, never uploaded by this script.
Right after a preview deploy IAP answers 403 until the SA's grant applies; each path retries a
403 every 15 s for up to 3 min, so a slow first call is expected.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import cloud_agent_gcp as G  # noqa: E402

URL = "https://operator-console-pr{pr}-887772634501.us-central1.run.app"
IAP_GRANT_RETRIES = 12


def goto_through_iap(page, url: str, retries: int = IAP_GRANT_RETRIES, wait_ms: int = 15_000):
    """The preview deploy's IAP grant for this SA takes a minute or two to apply: retry 403s."""
    resp = page.goto(url, wait_until="networkidle")
    for _ in range(retries):
        if not resp or resp.status != 403:
            break
        page.wait_for_timeout(wait_ms)
        resp = page.goto(url, wait_until="networkidle")
    return resp


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("paths", nargs="*", default=["/home"])
    ap.add_argument("--shots", type=Path, help="directory for full-page PNGs")
    ap.add_argument("--mobile", action="store_true", help="iPhone 14 WebKit instead of desktop Chromium")
    a = ap.parse_args(argv)

    from playwright.sync_api import sync_playwright

    token = G.sa_id_token(G.federated_access_token(G.cursor_oidc_token()))
    base = URL.format(pr=a.pr)
    if a.shots:
        a.shots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        engine = p.webkit if a.mobile else p.chromium
        opts = dict(p.devices["iPhone 14"]) if a.mobile else {"viewport": {"width": 1440, "height": 900}}
        browser = engine.launch()
        ctx = browser.new_context(**opts, extra_http_headers={"Authorization": f"Bearer {token}"})
        page = ctx.new_page()
        for path in a.paths:
            resp = goto_through_iap(page, base + path)
            final = page.url.removeprefix(base)
            print(f"{path}: {resp.status if resp else '?'} → {final} — {page.title()}")
            if a.shots:
                page.screenshot(path=str(a.shots / f"pr{a.pr}{path.replace('/', '-') or '-root'}.png"), full_page=True)
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
