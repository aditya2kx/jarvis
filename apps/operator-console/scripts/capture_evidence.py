#!/usr/bin/env python3
"""capture_evidence.py — Playwright screenshots of Operator Console for PR §4.

Uploads PNGs to the ``evidence-screenshots`` GitHub release (same staging bucket as
``agents/bhaga/grafana/capture_screenshot.py``) and prints viewable https URLs.

Usage:
    BYPASS_IAP_EMAIL=operator@mypalmetto.co npm run dev &
    python3 apps/operator-console/scripts/capture_evidence.py \\
        --path '/payroll?period=2026-06-29' --label payroll-unpaid-closed \\
        --scroll-to '[data-testid=tip-exemptions-editor]'

    # Element screenshot (avoids missing below-the-fold sections):
    --scroll-to SELECTOR scrolls into view then screenshots that node.

Exit non-zero if any capture or upload fails.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agents.bhaga.grafana.capture_screenshot import (  # noqa: E402
    _get_github_token,
    upload_screenshot,
)


def _capture_png(
    base_url: str,
    path: str,
    width: int,
    height: int,
    wait_ms: int,
    scroll_to: str | None,
) -> bytes:
    """Navigate; optionally scroll to selector; return PNG bytes."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        sys.exit(
            "ERROR: playwright not installed. Run: "
            "pip install playwright && python3 -m playwright install chromium\n"
            f"({e})"
        )

    url = base_url.rstrip("/") + (path if path.startswith("/") else f"/{path}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(url, wait_until="networkidle", timeout=60_000)
            if wait_ms > 0:
                page.wait_for_timeout(wait_ms)
            if scroll_to:
                loc = page.locator(scroll_to).first
                loc.wait_for(state="visible", timeout=30_000)
                loc.scroll_into_view_if_needed()
                page.wait_for_timeout(400)
                return loc.screenshot(type="png")
            # Prefer true document height — some layouts clip full_page.
            return page.screenshot(full_page=True, type="png")
        finally:
            browser.close()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Capture Operator Console pages and upload to GitHub for PR §4."
    )
    ap.add_argument(
        "--path",
        action="append",
        default=[],
        dest="paths",
        help="URL path incl. query (repeat with matching --label), e.g. /payroll",
    )
    ap.add_argument(
        "--label",
        action="append",
        default=[],
        dest="labels",
        help="Short label for the output filename (one per --path, in order)",
    )
    ap.add_argument(
        "--base-url",
        # localhost — not 127.0.0.1: Next.js 16 blocks cross-origin /_next from 127.0.0.1
        # (SSR HTML without hydration → Tip Exemptions Update never enables).
        default=os.environ.get("CONSOLE_BASE_URL", "http://localhost:3000"),
        help="Console origin (default CONSOLE_BASE_URL or http://localhost:3000)",
    )
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument(
        "--wait-ms",
        type=int,
        default=1500,
        help="Extra settle time after networkidle (default 1500)",
    )
    ap.add_argument(
        "--scroll-to",
        default=None,
        help="CSS selector to scroll into view and screenshot (element clip). "
        "Use for Tip Exemptions: [data-testid=tip-exemptions-editor]",
    )
    ap.add_argument(
        "--skip-upload",
        action="store_true",
        help="Save PNG locally only (no GitHub upload); print local path",
    )
    ap.add_argument("--output-dir", default="/tmp", help="Local PNG dir when --skip-upload")
    args = ap.parse_args()

    if not args.paths:
        ap.error("Specify at least one --path (e.g. --path /payroll)")

    labels = list(args.labels)
    while len(labels) < len(args.paths):
        labels.append(f"console-{len(labels)}")

    github_token = None if args.skip_upload else _get_github_token()
    results: list[tuple[str, str]] = []

    for path, label in zip(args.paths, labels):
        print(f"[capture] {label}: {args.base_url}{path}", file=sys.stderr)
        if args.scroll_to:
            print(f"[capture]   scroll-to {args.scroll_to}", file=sys.stderr)
        png = _capture_png(
            args.base_url, path, args.width, args.height, args.wait_ms, args.scroll_to,
        )
        print(f"[capture]   → {len(png)} bytes", file=sys.stderr)

        if args.skip_upload:
            out = pathlib.Path(args.output_dir) / f"{label}-{time.strftime('%Y%m%d-%H%M%S')}.png"
            out.write_bytes(png)
            url = str(out)
            print(f"[capture]   saved to {url}", file=sys.stderr)
        else:
            print("[capture]   uploading to GitHub…", file=sys.stderr)
            url = upload_screenshot(png, label, github_token)
            print(f"[capture]   → {url}", file=sys.stderr)

        results.append((label, url))

    for label, url in results:
        print(f"{label}: {url}")


if __name__ == "__main__":
    main()
