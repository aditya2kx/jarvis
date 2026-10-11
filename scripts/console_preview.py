#!/usr/bin/env python3
"""Open an Operator Console PR preview from a Cursor Cloud Agent VM — no Google sign-in.

    python3 scripts/console_preview.py --pr 385 /home /inventory --shots /tmp/shots [--mobile]

Authenticates to IAP with the cursor-cloud-agent ID token (scripts/cloud_agent_gcp.py id-token);
previews only — prod's IAP list does not include that identity. Prints one line per path
(status, final path, title); screenshots are local files, never uploaded by this script.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import cloud_agent_gcp as G  # noqa: E402

URL = "https://operator-console-pr{pr}-887772634501.us-central1.run.app"


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
            resp = page.goto(base + path, wait_until="networkidle")
            final = page.url.removeprefix(base)
            print(f"{path}: {resp.status if resp else '?'} → {final} — {page.title()}")
            if a.shots:
                page.screenshot(path=str(a.shots / f"pr{a.pr}{path.replace('/', '-') or '-root'}.png"), full_page=True)
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
