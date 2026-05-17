"""Playwright runtime helpers shared across all portal scrapers.

Centralizes:
    - **Ephemeral browser context launch** (no persistent profile). Every run
      starts from a fresh cookie jar — all auth happens via the keychain-
      backed login flows in each portal's runner. This matches what we
      already do for Google (OAuth refresh token) and ClickUp (PAT), and
      eliminates the multi-profile mess we used to have.
    - Download handling with deterministic filename + dir routing.
    - Failure-mode debugging: auto-screenshot + DOM snapshot before exceptions
      surface, so we always have evidence of what the page looked like when
      a selector failed.

Historical note: a previous iteration used a persistent profile at
`Jarvis/browser-profile/Profile 1` to skip the login flow and bypass 2FA
device-trust challenges. That created a multi-profile coupling problem
with the user-playwright MCP (which can't address non-Default profiles)
AND made cookies the single point of failure when they expired. As of
2026-05-17 we verified empirically that fresh-login via Playwright +
patchright + real Chrome channel + keychain password reaches the ADP
dashboard without 2FA prompts — so the persistent profile was retired.
The `launch_persistent` name is kept for API compatibility but now
launches an ephemeral context. If 2FA ever does fire, the failure path
surfaces a screenshot + Slack DM via the standard evidence capture.

NOT a portal-specific module. Square/ADP/etc. scrapers import these helpers
and add their own selectors + flow logic on top.
"""

from __future__ import annotations

import contextlib
import datetime
import os
import pathlib
import sys
import traceback
from typing import Iterator, Optional

try:
    # patchright is a stealth-patched Playwright fork that evades reCAPTCHA
    # fingerprinting (Square + ADP both trigger CAPTCHA on vanilla Playwright).
    # Drop-in API-compatible with playwright.sync_api.
    from patchright.sync_api import BrowserContext, Download, Page, sync_playwright  # type: ignore
    _RUNTIME_FLAVOR = "patchright"
except ImportError:
    from playwright.sync_api import BrowserContext, Download, Page, sync_playwright
    _RUNTIME_FLAVOR = "playwright"

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
DOWNLOADS_DIR = PROJECT_ROOT / "extracted" / "downloads"
SCREENSHOT_DIR = pathlib.Path.home() / ".bhaga" / "state" / "screenshots"

DEFAULT_VIEWPORT = {"width": 1440, "height": 900}
# A real-Chrome user-agent so ADP and Square don't flag us as headless/automation.
REAL_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
# Real Chrome launch args — disable automation-detection signals so ADP/Square
# don't flag the session as bot-like. We use channel="chrome" (real installed
# Chrome, not the bundled Chromium) for the same anti-bot reason.
_CHROME_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
]


@contextlib.contextmanager
def launch_persistent(
    portal: str,
    *,
    headed: bool = True,
    slow_mo_ms: int = 0,
    accept_downloads: bool = True,
    keep_open_on_error: bool = False,
) -> Iterator[tuple[BrowserContext, Page]]:
    """Launch an EPHEMERAL Chromium context for one portal.

    Name kept as `launch_persistent` for API compatibility with existing
    callers (download_timecard, download_earnings, square_tips.runner, etc.).
    Behavior changed 2026-05-17: no persistent profile, no cookies survive
    between runs. Each call starts from a fresh cookie jar; the portal's
    runner is responsible for completing the login flow via keychain creds.

    Yields (context, page). Both are torn down cleanly on with-block exit.

    On exception inside the with-block:
        * Captures a screenshot + page HTML + URL to ~/.bhaga/state/screenshots/
        * If keep_open_on_error=True, the browser stays open for manual debug
          (you must kill chrome yourself). Default closes cleanly.

    Usage (unchanged):
        with launch_persistent("square") as (ctx, page):
            page.goto("https://app.squareup.com/...")
            ...
    """
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    pw = sync_playwright().start()
    browser = None
    context: Optional[BrowserContext] = None
    try:
        # Ephemeral: launch a real Chrome browser, then create an isolated
        # context. No --user-data-dir → no persistent storage of any kind.
        browser = pw.chromium.launch(
            channel="chrome",
            headless=not headed,
            slow_mo=slow_mo_ms,
            args=_CHROME_LAUNCH_ARGS,
        )
        context = browser.new_context(
            viewport=DEFAULT_VIEWPORT,
            user_agent=REAL_UA,
            accept_downloads=accept_downloads,
        )
        page = context.new_page()
        try:
            yield context, page
        except Exception:
            _capture_failure_evidence(page, portal=portal)
            raise
    finally:
        if not keep_open_on_error:
            if context is not None:
                try:
                    context.close()
                except Exception:  # noqa: BLE001
                    pass
            if browser is not None:
                try:
                    browser.close()
                except Exception:  # noqa: BLE001
                    pass
        pw.stop()


def _capture_failure_evidence(page: Optional[Page], *, portal: str) -> None:
    """Save screenshot + HTML + URL for postmortem. Never raises."""
    if page is None:
        return
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    base = SCREENSHOT_DIR / f"{portal}-fail-{ts}"
    try:
        page.screenshot(path=str(base) + ".png", full_page=True)
    except Exception as e:  # noqa: BLE001
        print(f"[runtime] screenshot save failed: {e}", file=sys.stderr)
    try:
        (base.with_suffix(".html")).write_text(page.content())
    except Exception as e:  # noqa: BLE001
        print(f"[runtime] html save failed: {e}", file=sys.stderr)
    try:
        url = page.url
        (base.with_suffix(".meta.txt")).write_text(
            f"url={url}\ntimestamp={ts}\nportal={portal}\n\n"
            f"traceback:\n{traceback.format_exc()}\n"
        )
    except Exception as e:  # noqa: BLE001
        print(f"[runtime] meta save failed: {e}", file=sys.stderr)
    print(f"[runtime] failure evidence saved to {base}.*", file=sys.stderr)


def download_to(
    page: Page,
    *,
    trigger: callable,  # type: ignore[valid-type]
    save_dir: Optional[pathlib.Path] = None,
    rename_to: Optional[str] = None,
    timeout_ms: int = 60_000,
) -> pathlib.Path:
    """Trigger a download via `trigger()` and save it deterministically.

    `trigger` is a zero-arg callable that performs the click/keypress that
    starts the download. Playwright captures the download as it fires.

    Returns the final saved path. The original suggested filename is used
    unless `rename_to` is provided. `save_dir` defaults to DOWNLOADS_DIR.

    Example:
        path = download_to(page, trigger=lambda: page.click("#download-btn"))
    """
    target_dir = save_dir or DOWNLOADS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    with page.expect_download(timeout=timeout_ms) as dl_info:
        trigger()
    download: Download = dl_info.value
    filename = rename_to or download.suggested_filename
    dest = target_dir / filename
    download.save_as(str(dest))
    return dest


def is_logged_in(page: Page, *, sentinel_url_substring: str, timeout_ms: int = 5_000) -> bool:
    """Check whether the persistent profile is still logged in.

    Navigates to the portal's dashboard URL pattern; if redirected to login,
    we're logged out.
    """
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:  # noqa: BLE001
        pass
    return sentinel_url_substring in page.url
