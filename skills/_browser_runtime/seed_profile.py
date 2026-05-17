"""Interactive profile seeder for portal scrapers.

Run this ONCE per portal per machine to bake login cookies into the persistent
Chromium profile. After seeding, the daily scrapers reuse the cookies and
skip the login flow entirely (unless ADP forces re-auth, which it does
periodically — see retry guidance in agents/bhaga/README.md OPS section).

Usage:
    python3 -m skills._browser_runtime.seed_profile --portal square --url https://app.squareup.com/login
    python3 -m skills._browser_runtime.seed_profile --portal adp    --url https://runpayroll.adp.com

The flow:
    1. Opens a visible Chromium window pointed at the portal's login URL.
    2. YOU log in manually (username, password, MFA if needed). Tick any
       "trust this device" or "remember me" boxes — they let cookies persist.
    3. Once you're at the dashboard, press ENTER in this terminal.
    4. Script verifies the URL matches the post-login pattern and closes
       the browser cleanly. Cookies are saved to the profile dir.
"""

from __future__ import annotations

import argparse
import sys

from .runtime import launch_persistent


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--portal", required=True, help="Portal nickname (e.g. 'square', 'adp').")
    cli.add_argument("--url", required=True, help="Initial URL to open.")
    cli.add_argument(
        "--verify-substring", default=None,
        help="After you press Enter, verify the page URL contains this substring (e.g. 'dashboard').",
    )
    args = cli.parse_args()

    print(f"\nOpening {args.url} in persistent profile {args.portal!r}...")
    print("A Chromium window will appear. LOG IN manually in that window.")
    print("Tick any 'remember me' / 'trust this device' boxes you see.")
    print("When you reach the post-login dashboard, COME BACK HERE and press Enter.\n")

    with launch_persistent(args.portal, headed=True) as (ctx, page):
        page.goto(args.url)
        input("Press Enter once you've finished logging in and reached the dashboard... ")
        current = page.url
        print(f"\nFinal page URL: {current}")
        if args.verify_substring and args.verify_substring not in current:
            print(
                f"\nWARNING: URL does not contain expected substring "
                f"{args.verify_substring!r}. You may not be fully logged in. "
                f"Inspect the window, then close it and re-run.",
                file=sys.stderr,
            )
            input("Press Enter to close the browser anyway (cookies up to now will be saved)... ")
            return 1
        print("\nLooks good. Closing browser; cookies will persist for nightly scrapes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
