"""Playwright runtime helpers shared across all portal scrapers.

Centralizes:
    - **Ephemeral browser context launch** (no persistent profile). Every run
      starts from a fresh cookie jar — all auth happens via the keychain-
      backed login flows in each portal's runner. This matches what we
      already do for Google (OAuth refresh token) and ClickUp (PAT), and
      eliminates the multi-profile mess we used to have.
    - **Timezone-locked context**: every browser context is created with
      ``timezone_id="America/Chicago"`` and ``locale="en-US"``, regardless
      of where the operator's laptop physically is. The shop is in Austin
      TX (CT); the operator travels (e.g. IST, ET); and all portal date
      filters (Square date-range picker, ADP report ranges, etc.) are
      interpreted in BROWSER timezone. If we don't pin this, a portal in
      a non-CT browser will silently truncate exports at the wrong wall-
      clock boundary. All of BHAGA's downstream date arithmetic
      (data_window_end, refresh_date, gap windows) is CT-anchored, so
      the browser must agree.
      Incident reference (2026-05-22): operator was in IST, Square's
      date-range filter interpreted "5/21 to 5/23" as IST dates, the
      export ended at IST 5/22 23:59:59 = CT 5/22 13:29:59, and silently
      dropped the entire CT 5/22 afternoon (~$970 of sales / half a day).
      Locking the context to America/Chicago + en-US makes the browser
      behave identically no matter where the laptop is.
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

# Central Time anchor for "today's mtime" checks. We use CT (not UTC) because
# the daily refresh is scheduled at 9 PM CT and operators reason about
# "today's scrape" in CT.
try:
    from zoneinfo import ZoneInfo  # py3.9+
    _CT_TZ = ZoneInfo("America/Chicago")
except ImportError:  # pragma: no cover
    _CT_TZ = None


def today_ct_midnight_epoch() -> float:
    """Return the epoch-seconds timestamp for CT-today's 00:00:00.

    Used by `is_fresh_download` to decide whether a previously-downloaded
    file was produced "today" in CT terms (the operator's mental model).
    Files written before this timestamp are considered stale and will be
    re-downloaded.
    """
    import datetime as _dt
    if _CT_TZ is None:
        # Fallback: local midnight. On the production laptop local is CT
        # so this matches; on a CI host it might not, which is fine since
        # this code only runs in BHAGA's nightly path.
        now = _dt.datetime.now()
        midnight = _dt.datetime.combine(now.date(), _dt.time.min)
        return midnight.timestamp()
    now_ct = _dt.datetime.now(tz=_CT_TZ)
    midnight_ct = _dt.datetime.combine(now_ct.date(), _dt.time.min, tzinfo=_CT_TZ)
    return midnight_ct.timestamp()


def is_fresh_download(path: pathlib.Path, *, min_bytes: int = 100) -> bool:
    """True if `path` exists, was modified after CT-midnight today, and is non-trivial.

    `min_bytes` filters out empty/error stubs (Square's CSV header alone is
    ~70 bytes, so 100 is the floor for a meaningful file). Caller is
    responsible for any further parse-validation; this check is cheap and
    suitable for the pre-scrape skip-shortcut in download_* functions.
    """
    try:
        st = path.stat()
    except FileNotFoundError:
        return False
    if st.st_size < min_bytes:
        return False
    if st.st_mtime < today_ct_midnight_epoch():
        return False
    return True

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


def _resolve_browser_channel() -> Optional[str]:
    """Determine the Chromium channel to use at launch time.

    Resolution order:
      1. BHAGA_BROWSER_CHANNEL env var (explicit override)
      2. "chrome" if a real Google Chrome installation is detected
      3. None → use the patchright-bundled Chromium (Docker / CI)
    """
    env_channel = os.environ.get("BHAGA_BROWSER_CHANNEL")
    if env_channel:
        return env_channel if env_channel.lower() != "bundled" else None

    chrome_paths = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/opt/google/chrome/chrome",
        "/usr/bin/google-chrome",
    ]
    for p in chrome_paths:
        if os.path.exists(p):
            return "chrome"
    return None


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
        # Ephemeral: launch a Chromium-family browser, then create an isolated
        # context. No --user-data-dir → no persistent storage of any kind.
        # In Docker/CI where only patchright-bundled Chromium is available,
        # channel=None causes patchright to use its own binary.
        channel = _resolve_browser_channel()
        browser = pw.chromium.launch(
            channel=channel,
            headless=not headed,
            slow_mo=slow_mo_ms,
            args=_CHROME_LAUNCH_ARGS,
        )
        context = browser.new_context(
            viewport=DEFAULT_VIEWPORT,
            user_agent=REAL_UA,
            accept_downloads=accept_downloads,
            # Pin TZ + locale so portal date filters always interpret ranges
            # in CT regardless of the operator's physical location. See
            # module docstring (2026-05-22 IST-truncation incident).
            timezone_id="America/Chicago",
            locale="en-US",
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
