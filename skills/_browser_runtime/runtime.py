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
import time
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

# Extra Chromium flags applied ONLY in headless/container environments (Cloud
# Run, Docker, CI). These harden the launch against the most common container
# crash: `TargetClosedError` on startup caused by the tiny default /dev/shm
# (64 MB) — `--disable-dev-shm-usage` routes shared memory to /tmp instead.
# `--no-sandbox` is required because Cloud Run already sandboxes the container
# and the Chrome sandbox needs user namespaces that aren't available there.
# These are deliberately NOT applied on the operator's laptop (headed, real
# Chrome channel) so the anti-bot fingerprint of a real browser is preserved.
_HEADLESS_STABILITY_ARGS = [
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-gpu",
]


def _launch_retries() -> int:
    """Number of browser-launch attempts before giving up. Config-driven."""
    try:
        return max(1, int(os.environ.get("BHAGA_BROWSER_LAUNCH_RETRIES", "3")))
    except (TypeError, ValueError):
        return 3


def _launch_backoff_ms() -> int:
    """Base backoff between launch retries (exponential per attempt). 0 in tests."""
    try:
        return max(0, int(os.environ.get("BHAGA_BROWSER_LAUNCH_BACKOFF_MS", "1000")))
    except (TypeError, ValueError):
        return 1000


def _launch_args(headed: bool) -> list[str]:
    """Assemble Chromium launch args, adding container-stability flags headless."""
    args = list(_CHROME_LAUNCH_ARGS)
    if not headed or _force_headless():
        args += _HEADLESS_STABILITY_ARGS
    return args


def _is_retryable_launch_error(exc: BaseException) -> bool:
    """True only for transient browser-LAUNCH infra failures (crash on startup,
    launch timeout).

    Auth / 2FA / page-logic errors never reach the launch path — they are raised
    inside the caller's `with` body, never during setup — and must NEVER be
    auto-retried (a retry could re-fire an OTP/SMS; see HL#8). This classifier is
    intentionally narrow: it matches the Chromium-died-on-launch signatures only.
    """
    name = type(exc).__name__
    msg = str(exc).lower()
    if name == "TargetClosedError":
        return True
    if name == "TimeoutError" and "launch" in msg:
        return True
    if "target page, context or browser has been closed" in msg:
        return True
    if "browsertype.launch" in msg or "browser has been closed" in msg:
        return True
    return False


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


def _force_headless() -> bool:
    """True when the environment demands headless mode.

    Checked inside launch_persistent to override the caller's ``headed``
    flag. Cloud Run sets ``BHAGA_HEADLESS=1``; also auto-detects Docker
    containers (/.dockerenv) and missing DISPLAY (no X server at all).
    """
    if os.environ.get("BHAGA_HEADLESS", "").strip() in ("1", "true", "yes"):
        return True
    if os.path.exists("/.dockerenv"):
        return True
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        return True
    return False


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
    if _force_headless():
        headed = False
        print(f"[runtime] headless forced by environment (portal={portal})", file=sys.stderr)

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    # Setup (start driver + launch + context + page) is retried on transient
    # launch crashes; the yielded BODY is never retried (it may have side
    # effects — downloads, OTPs). See _start_browser_session.
    pw, browser, context, page = _start_browser_session(
        portal,
        headed=headed,
        slow_mo_ms=slow_mo_ms,
        accept_downloads=accept_downloads,
    )
    try:
        try:
            yield context, page
        except Exception:
            _capture_failure_evidence(page, portal=portal)
            raise
    finally:
        if not keep_open_on_error:
            try:
                context.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass
        pw.stop()


def _start_browser_session(
    portal: str,
    *,
    headed: bool,
    slow_mo_ms: int,
    accept_downloads: bool,
):
    """Start Playwright, launch Chromium, and open an isolated context+page,
    retrying ONLY on transient launch-infra crashes.

    Each attempt restarts the whole Playwright driver (a ``TargetClosedError``
    at launch can leave the driver wedged, so re-calling ``launch`` on the same
    driver is unreliable). On the final attempt — or on a non-retryable error —
    the exception propagates. Returns ``(pw, browser, context, page)``; the
    caller owns teardown.

    Leaves a breadcrumb (precise, greppable, distinct from dbus/crashpad noise)
    on every failed attempt and on recovery, so an investigator on another
    machine can diagnose from logs alone.
    """
    retries = _launch_retries()
    backoff_ms = _launch_backoff_ms()
    channel = _resolve_browser_channel()

    for attempt in range(1, retries + 1):
        pw = sync_playwright().start()
        browser = None
        try:
            # Ephemeral: launch a Chromium-family browser, then create an
            # isolated context. No --user-data-dir → no persistent storage.
            # In Docker/CI where only patchright-bundled Chromium is available,
            # channel=None causes patchright to use its own binary.
            browser = pw.chromium.launch(
                channel=channel,
                headless=not headed,
                slow_mo=slow_mo_ms,
                args=_launch_args(headed),
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
            if attempt > 1:
                print(
                    f"[runtime] {portal} chromium launch recovered on attempt "
                    f"{attempt}/{retries}",
                    file=sys.stderr,
                )
            return pw, browser, context, page
        except Exception as exc:  # noqa: BLE001
            # Tear down the wedged driver before retrying / propagating.
            if browser is not None:
                try:
                    browser.close()
                except Exception:  # noqa: BLE001
                    pass
            try:
                pw.stop()
            except Exception:  # noqa: BLE001
                pass

            retryable = _is_retryable_launch_error(exc)
            if not retryable or attempt == retries:
                print(
                    f"[runtime] {portal} chromium launch failed "
                    f"(attempt {attempt}/{retries}): {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
                raise
            wait_s = (backoff_ms * (2 ** (attempt - 1))) / 1000.0
            print(
                f"[runtime] {portal} chromium launch failed "
                f"(attempt {attempt}/{retries}): {type(exc).__name__}: {exc}; "
                f"retrying in {wait_s:.1f}s",
                file=sys.stderr,
            )
            if wait_s > 0:
                time.sleep(wait_s)


def browser_healthcheck(*, portal: str = "healthcheck") -> bool:
    """Pre-flight smoke test: can we launch Chromium and open about:blank?

    Call this BEFORE driving any OTP-gated portal so a crashy browser is
    detected (and retried, via the same _start_browser_session path) before an
    OTP/SMS is spent on a session that would crash anyway. Returns True if the
    browser is healthy, False otherwise. Never raises.
    """
    headed = not _force_headless()
    try:
        pw, browser, context, page = _start_browser_session(
            portal, headed=headed, slow_mo_ms=0, accept_downloads=False
        )
    except Exception as exc:  # noqa: BLE001
        print(
            f"[runtime] browser healthcheck FAILED: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return False
    try:
        page.goto("about:blank")
        return True
    except Exception as exc:  # noqa: BLE001
        print(
            f"[runtime] browser healthcheck FAILED post-launch: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return False
    finally:
        try:
            context.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            browser.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            pw.stop()
        except Exception:  # noqa: BLE001
            pass


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
