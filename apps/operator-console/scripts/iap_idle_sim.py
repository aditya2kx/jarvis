#!/usr/bin/env python3
"""iap_idle_sim.py — Issue #208 §4 E2 idle-sim for Operator Console IAP.

Simulates "return after idle" without waiting hours:
  1. Keep Google session cookies
  2. Wipe IAP cookies on the canonical console host
  3. Open the canonical URL
  4. If Google account chooser appears, click --email
  5. Assert we land on the Operator Console

Usage (needs a Chromium profile that is already signed into Google):

    python3 apps/operator-console/scripts/iap_idle_sim.py \\
        --email aditya.2ky@gmail.com \\
        --user-data-dir ~/Library/Application\\\\ Support/Google/Chrome

Or with a Playwright storage state that includes Google cookies:

    python3 apps/operator-console/scripts/iap_idle_sim.py \\
        --email aditya.2ky@gmail.com --storage-state /tmp/google-state.json

Exit 0 on pass; non-zero on fail. No BQ / tip / payroll side effects.
"""

from __future__ import annotations

import argparse
import re
import sys
import time

CANONICAL_HOST = "operator-console-887772634501.us-central1.run.app"
CANONICAL_ORIGIN = f"https://{CANONICAL_HOST}"


def _is_console(url: str) -> bool:
    return CANONICAL_HOST in url and "accounts.google.com" not in url


def _is_chooser(url: str) -> bool:
    return "accounts.google.com" in url and (
        "accountchooser" in url or "signin" in url or "InteractiveLogin" in url
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="IAP idle-sim (Issue #208 E2)")
    ap.add_argument("--email", required=True, help="Allowlisted Google account to click")
    ap.add_argument("--base-url", default=CANONICAL_ORIGIN)
    ap.add_argument("--storage-state", default=None, help="Playwright storage state JSON")
    ap.add_argument("--user-data-dir", default=None, help="Chromium user-data-dir with Google session")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--timeout-ms", type=int, default=90_000)
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        print(
            "ERROR: playwright not installed. "
            "pip install playwright && python3 -m playwright install chromium\n"
            f"({e})",
            file=sys.stderr,
        )
        return 2

    base = args.base_url.rstrip("/")
    with sync_playwright() as p:
        if args.user_data_dir:
            context = p.chromium.launch_persistent_context(
                args.user_data_dir,
                headless=not args.headed,
                channel="chrome",
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = context.pages[0] if context.pages else context.new_page()
            browser = None
        else:
            browser = p.chromium.launch(headless=not args.headed)
            context = browser.new_context(
                storage_state=args.storage_state if args.storage_state else None
            )
            page = context.new_page()

        try:
            # Wipe IAP cookies on canonical (idle) — keep Google cookies.
            remaining = []
            for c in context.cookies():
                if "operator-console" in c.get("domain", "") or re.search(
                    r"GCP_IAP|__Host-GCP_IAP", c.get("name", ""), re.I
                ):
                    continue
                remaining.append(c)
            context.clear_cookies()
            if remaining:
                context.add_cookies(remaining)

            page.goto(f"{base}/", wait_until="domcontentloaded", timeout=args.timeout_ms)
            page.wait_for_timeout(1500)
            url = page.url

            if _is_console(url):
                title = page.title()
                ok = "Operator Console" in title or "Palmetto" in title
                print(f"PASS: already admitted after idle wipe → {url} title={title!r}")
                return 0 if ok else 1

            if not _is_chooser(url):
                body = page.locator("body").inner_text()[:400]
                print(f"FAIL: expected chooser or console, got {url}\n{body}", file=sys.stderr)
                return 1

            # Click allowlisted account
            acct = page.get_by_role("link", name=re.compile(re.escape(args.email), re.I))
            if acct.count() == 0:
                # Fallback: any element containing the email
                acct = page.locator(f"text={args.email}")
            if acct.count() == 0:
                print(f"FAIL: account {args.email!r} not visible on chooser", file=sys.stderr)
                return 1
            acct.first.click(timeout=15_000)

            # Wait for console (or IAP error)
            deadline = time.time() + args.timeout_ms / 1000.0
            while time.time() < deadline:
                page.wait_for_timeout(500)
                url = page.url
                if _is_console(url) and "AUTHENTICATING" not in url:
                    break
                body = page.locator("body").inner_text()
                if re.search(r"Error code\s*9|malformed|should not be retried", body, re.I):
                    print(f"FAIL: auth error page at {url}\n{body[:500]}", file=sys.stderr)
                    return 1

            url = page.url
            title = page.title()
            body = page.locator("body").inner_text()[:300]
            if not _is_console(url) or "AUTHENTICATING" in url:
                print(f"FAIL: did not reach console: {url}\n{body}", file=sys.stderr)
                return 1
            if re.search(r"Error code\s*9|problem with your request", body, re.I):
                print(f"FAIL: Error code 9 after account tap: {url}", file=sys.stderr)
                return 1
            print(f"PASS: idle-sim → {url} title={title!r}")
            return 0
        finally:
            context.close()
            if browser is not None:
                browser.close()


if __name__ == "__main__":
    sys.exit(main())
