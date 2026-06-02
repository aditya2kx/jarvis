"""Standalone Playwright runner for the Square Transactions CSV scrape.

Replaces the MCP-driven build_plan() flow with a deterministic Python
function that downloads the Transactions CSV for a given date range using
a persistent browser profile (cookies survive between runs; no daily login).

Public entry point:
    download_transactions(start_date, end_date) -> Path

Usage (CLI for manual / debug):
    python3 -m skills.square_tips.runner --start 2026-05-14 --end 2026-05-15
    python3 -m skills.square_tips.runner --start 2026-05-15 --end 2026-05-15 --keep-open

Selectors are sourced from skills/square_tips/selectors/transactions.json so
when Square changes the UI we update one file, not two.
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from skills._browser_runtime.runtime import (
    DOWNLOADS_DIR,
    download_to,
    is_fresh_download,
    launch_persistent,
)
from skills.square_tips.transactions_backend import parse_csv, get_credentials

LOGIN_URL = "https://app.squareup.com/login"
TRANSACTIONS_URL = "https://app.squareup.com/dashboard/sales/transactions"
ITEM_SALES_URL = "https://app.squareup.com/dashboard/sales/reports/item-sales"
KDS_PERFORMANCE_URL = "https://app.squareup.com/dashboard/kitchen/reports/performance"

_SELECTORS_DIR = pathlib.Path(__file__).resolve().parent / "selectors"

# Built-in fallback so a missing/partial selectors JSON never hard-crashes the
# scrape. The JSON file is the SOURCE OF TRUTH for drift fixes; these defaults
# only fill gaps. Keep them in sync with selectors/item_sales.json["selectors"].
_ITEM_SALES_SELECTOR_DEFAULTS = {
    "date_picker": {
        "pill_text_patterns": [
            r"\d{2}/\d{2}/\d{4}",
            r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d",
            r"(Today|Yesterday|This week|Last week|This month|Last month|This year|Last year|Custom)",
        ],
        "pill_extra_locators": [
            "button[aria-haspopup='dialog']",
            "button[aria-haspopup='true']",
            "[data-testid*='date'] button",
            "button[class*='date-range']",
        ],
        "pill_wait_timeout_ms": 15000,
        "pill_pattern_attempt_timeout_ms": 5000,
        "post_open_wait_ms": 800,
        "date_input_selector": "input[type='text']:visible",
        "date_input_label_keywords": ["date", "start", "end"],
    },
    "export": {
        "export_button_patterns": [r"^Export$", "Export"],
        "detail_csv_patterns": [r"Detail\s+CSV", "Detail CSV Export"],
        "menu_open_wait_ms": 800,
        "download_timeout_ms": 120000,
    },
}

_item_sales_selectors_cache: dict | None = None


def _item_sales_selectors() -> dict:
    """Load (and cache) the machine-loadable item-sales selector block.

    Source of truth is ``selectors/item_sales.json`` → ``selectors`` — so fixing
    Square UI drift (e.g. the 2026-05-31 'date picker not found' incident) is a
    one-file edit, no code change. Missing file/keys fall back to
    ``_ITEM_SALES_SELECTOR_DEFAULTS`` per sub-block so a partial/malformed JSON
    never hard-crashes the scrape.
    """
    global _item_sales_selectors_cache
    if _item_sales_selectors_cache is not None:
        return _item_sales_selectors_cache

    merged = {k: dict(v) for k, v in _ITEM_SALES_SELECTOR_DEFAULTS.items()}
    try:
        raw = json.loads((_SELECTORS_DIR / "item_sales.json").read_text())
        loaded = raw.get("selectors", {}) or {}
        for block in ("date_picker", "export"):
            merged[block] = {**merged[block], **(loaded.get(block, {}) or {})}
    except Exception as exc:  # noqa: BLE001
        print(
            f"[square_item_sales] WARN: could not load selectors/item_sales.json "
            f"({exc}); using built-in selector defaults",
            file=sys.stderr,
        )
    _item_sales_selectors_cache = merged
    return _item_sales_selectors_cache


def _find_item_sales_pill(page, *, total_timeout_ms: int | None = None):
    """Return a visible locator for the item-sales date-range pill, or ``None``.

    JSON-driven + resilient: tries each ``pill_text_patterns`` entry on a
    ``<button>`` first, then the structural ``pill_extra_locators`` (aria-haspopup
    / data-testid / class). A single Square label-or-format change is absorbed by
    adding one pattern to ``selectors/item_sales.json`` — no code edit.
    """
    dp = _item_sales_selectors()["date_picker"]
    per = dp.get("pill_pattern_attempt_timeout_ms", 5000)
    for pat in dp.get("pill_text_patterns", []):
        loc = page.locator("button").filter(has_text=re.compile(pat)).first
        try:
            loc.wait_for(state="visible", timeout=per)
            return loc
        except Exception:
            continue
    for css in dp.get("pill_extra_locators", []):
        try:
            loc = page.locator(css).first
            loc.wait_for(state="visible", timeout=per)
            return loc
        except Exception:
            continue
    return None


def _is_on_login(url: str) -> bool:
    """True if we're on the auth flow (path starts with /login)."""
    # app.squareup.com/login?return_to=%2Fdashboard%2F... — path is /login.
    return "squareup.com/login" in url


def _is_on_dashboard(url: str) -> bool:
    """True if we're on a dashboard page (real path /dashboard, not just a query param)."""
    return "squareup.com/dashboard" in url and "squareup.com/login" not in url


def _dismiss_cookie_banner(page) -> None:
    """OneTrust cookie banner sometimes blocks the login form. Accept all + move on.

    No-op if the banner isn't present. Never raises."""
    try:
        btn = page.locator("#accept-recommended-btn-handler, #onetrust-accept-btn-handler").first
        if btn.is_visible(timeout=2_000):
            btn.click()
            page.wait_for_timeout(500)
    except Exception:  # noqa: BLE001
        pass


_SCRAPE_LOCK = "/tmp/bhaga-square-scrape.lock"


def _acquire_scrape_lock(store: str) -> None:
    """Refuse to start a Square scrape if another one is already in flight.

    Prevents the "duplicate SMS" failure mode: previously, accidentally
    launching a second scrape process while a first was mid-2FA would fire
    a SECOND SMS to the operator and burn a second OTP reply. Now any
    second invocation hard-fails before opening a browser.

    Lock format: `<pid> <store> <started_iso>` in a file at /tmp/. If the
    lock file exists but the PID is no longer alive (e.g. the previous
    scrape was killed with SIGKILL), reclaim it.
    """
    import os
    if os.path.exists(_SCRAPE_LOCK):
        try:
            with open(_SCRAPE_LOCK) as f:
                pid_str, *_ = f.read().split()
            pid = int(pid_str)
            os.kill(pid, 0)  # raises if pid not alive
            raise RuntimeError(
                f"Square scrape already in progress (pid={pid}). "
                f"Two scrapes would each fire a 2FA SMS. "
                f"Wait for the first to finish, or kill it explicitly: kill {pid} && rm {_SCRAPE_LOCK}"
            )
        except (ProcessLookupError, ValueError, OSError, FileNotFoundError):
            # Stale lock — previous process died, reclaim.
            pass
    with open(_SCRAPE_LOCK, "w") as f:
        f.write(f"{os.getpid()} {store} {datetime.datetime.utcnow().isoformat()}Z\n")


def _release_scrape_lock() -> None:
    """Remove the scrape lock. Idempotent; safe to call from finally blocks."""
    try:
        import os
        os.remove(_SCRAPE_LOCK)
    except (FileNotFoundError, OSError):
        pass


def _ensure_logged_in(page, *, store: str) -> None:
    """Navigate to Transactions. If bounced to login, run the 2-step credential flow.

    Handles Square's two-step flow: email -> Continue -> password -> Sign in.
    Also dismisses the OneTrust cookie banner if it appears.
    """
    page.goto(TRANSACTIONS_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(1_500)  # let any redirect settle
    _dismiss_cookie_banner(page)
    if _is_on_dashboard(page.url):
        return

    if not _is_on_login(page.url):
        # Something unexpected (captcha, maintenance page, etc.)
        raise RuntimeError(
            f"Square did not land on /login or /dashboard. Current URL: {page.url}"
        )

    creds = get_credentials(store)
    # Email step — Square uses MPUI inputs, not standard type="email".
    email_input = page.locator(
        "[data-testid='username-input'], input#mpui-combo-field-input"
    ).first
    email_input.wait_for(state="visible", timeout=10_000)
    email_input.fill(creds["username"])
    page.get_by_role("button", name=re.compile(r"continue|sign\s*in", re.I)).first.click()

    # Password step — visible input[type=password]. The hidden one with tabindex=-1 is decoy.
    pw_input = page.locator("input[type='password']:not([tabindex='-1'])").first
    pw_input.wait_for(state="visible", timeout=15_000)
    pw_input.fill(creds["password"])
    page.keyboard.press("Enter")

    # After password submit, EITHER we land on /dashboard OR Square shows the
    # 2FA delivery picker (still under /login/...). Wait for either.
    try:
        page.wait_for_function(
            """() => {
                const path = window.location.pathname;
                if (path.startsWith('/dashboard')) return true;
                // 2FA picker text — appears regardless of URL still being /login
                const body = document.body && document.body.innerText || '';
                return /how would you like to receive the code/i.test(body)
                    || /text me the code/i.test(body);
            }""",
            timeout=30_000,
        )
    except Exception:
        # Neither dashboard nor 2FA picker — could be the inline "wrong
        # password" banner or something else. Let the dashboard check below
        # surface it with a screenshot.
        pass
    page.wait_for_timeout(1_500)

    if _is_on_dashboard(page.url):
        return

    # 2FA challenge — operator-in-the-loop via Slack DM.
    _handle_square_two_factor(page, store=store)

    if not _is_on_dashboard(page.url):
        try:
            ts = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
            snap = f"/Users/adityaparikh/.bhaga/state/screenshots/square-{store}-authfail-{ts}.png"
            page.screenshot(path=snap, full_page=True)
        except Exception:  # noqa: BLE001
            snap = "(screenshot capture failed)"
        raise RuntimeError(
            f"Square login did not reach /dashboard even after 2FA flow. "
            f"Current URL: {page.url}\nScreenshot: {snap}"
        )


def _handle_square_two_factor(page, *, store: str) -> None:
    """Drive Square's SMS-OTP 2FA flow with operator-in-the-loop via Slack.

    Steps (called only when login lands on the OTP picker screen):
        1. Pick "Text me the code (SMS)" radio (default — Phone call fallback
           is left unhandled today; per policy SMS is always first choice).
        2. Click Continue. Square sends the SMS to ...0038.
        3. Wait for the 6-digit code input to render on the next screen.
        4. Slack-DM the operator via skills.slack.adapter.request_otp; the
           BHAGA listener daemon picks up the user's reply over Socket Mode
           and unblocks read_otp().
        5. Fill the code, submit.

    Timeout is generous (30 min) — operator's phone may be in another room.
    The flow runs once per fresh-device session; once Square's "trust this
    device for 30 days" flow runs, future logins from the same IP should
    skip the challenge entirely (we don't tick the trust-checkbox today
    because the cookie would be lost on context teardown anyway).
    """
    import re as _re  # local re-import (re is already imported at module top)

    # Step 1: pick SMS radio. Square uses native radio inputs.
    try:
        sms_radio = page.get_by_role(
            "radio", name=_re.compile(r"text\s*me\s*the\s*code|SMS", _re.I)
        ).first
        sms_radio.wait_for(state="visible", timeout=10_000)
        sms_radio.check()
    except Exception:
        # Maybe only one delivery method — skip picker. Or selector drift.
        # Try clicking the visible label as a fallback.
        try:
            page.get_by_text(_re.compile(r"text\s*me\s*the\s*code", _re.I)).first.click()
        except Exception:
            pass

    # Step 2: click Continue. Triggers SMS send.
    try:
        page.get_by_role(
            "button", name=_re.compile(r"^continue$|^send$|^next$", _re.I)
        ).first.click()
    except Exception:
        page.keyboard.press("Enter")

    # Step 3: wait for the code-entry input. Square uses 6 separate digit boxes
    # OR a single text input depending on the surface; cover both.
    page.wait_for_timeout(3_000)
    code_input = None
    for candidate in [
        "input[autocomplete='one-time-code']",
        "input[name='code']",
        "input[type='text'][maxlength='6']",
        "input[inputmode='numeric']",
    ]:
        loc = page.locator(candidate).first
        try:
            loc.wait_for(state="visible", timeout=4_000)
            code_input = loc
            break
        except Exception:
            continue
    if code_input is None:
        # Could be a 6-box widget; collect all visible digit inputs.
        digit_inputs = page.locator("input[type='text'][maxlength='1']")
        if digit_inputs.count() < 4:
            raise RuntimeError(
                "Square 2FA code-entry input not found. Selector drift. "
                f"Current URL: {page.url}"
            )

    # Step 4: request the code via Slack DM, block for reply.
    from skills.slack.adapter import request_otp  # local import: optional dep

    # In the READY-handshake model the orchestrator has already confirmed the
    # operator is at their phone before we get here, so the bounded wait is
    # short (BHAGA_OTP_WAIT_S, default 900s). Standalone/legacy callers with no
    # env set fall back to the generous 1800s.
    import os as _os
    wait_s = int(_os.environ.get("BHAGA_OTP_WAIT_S", "1800"))
    print(f"[square 2fa] requesting OTP via Slack for store={store!r} (wait={wait_s}s)...")
    code = request_otp(
        user_id="U0APJRE5DC4",          # operator (primary_user_id from config.yaml)
        portal_name="Square",
        timeout_seconds=wait_s,
        phone_hint="+1-XXX-XXX-0038",
        agent="bhaga",
    )
    if not code:
        raise RuntimeError(
            "Square 2FA: operator did not reply with the OTP within 30 minutes. "
            "Either retry the scrape (a new SMS will fire) or complete login manually."
        )
    code = code.strip().replace(" ", "").replace("-", "")
    print(f"[square 2fa] got code (len={len(code)}); submitting.")

    # Step 5: fill & submit.
    if code_input is not None:
        code_input.fill(code)
    else:
        digit_inputs = page.locator("input[type='text'][maxlength='1']")
        for i, ch in enumerate(code):
            try:
                digit_inputs.nth(i).fill(ch)
            except Exception:
                break
    page.keyboard.press("Enter")

    # Wait for dashboard.
    try:
        page.wait_for_function(
            "() => location.pathname.startsWith('/dashboard')",
            timeout=30_000,
        )
    except Exception:
        # Some flows have an interstitial "Trust this device?" screen — skip it
        # with a "Not now" / "Skip" if visible.
        try:
            page.get_by_role("button", name=_re.compile(r"not\s*now|skip|later", _re.I)).first.click(timeout=3_000)
            page.wait_for_function(
                "() => location.pathname.startsWith('/dashboard')",
                timeout=20_000,
            )
        except Exception:
            pass  # fall through to caller's dashboard check


def _set_date_range(page, *, start: datetime.date, end: datetime.date) -> None:
    """Open the date-picker popover and type a precise start/end range."""
    # The date-range pill is a button whose visible text matches MM/DD/YYYY...
    # Use .filter(has_text=regex) — avoids the role-name regex parser that
    # chokes on slashes in patchright's selector engine.
    pill = page.locator("button").filter(has_text=re.compile(r"\d{2}/\d{2}/\d{4}")).first
    pill.wait_for(state="visible", timeout=15_000)
    pill.click()
    page.wait_for_timeout(800)

    # Two text inputs inside the popover. Start, then End.
    start_str = start.strftime("%m/%d/%Y")
    end_str = end.strftime("%m/%d/%Y")

    inputs = page.locator("input[type='text']:visible")
    n = inputs.count()
    date_inputs = []
    for i in range(n):
        el = inputs.nth(i)
        val = el.input_value() or ""
        label = (el.get_attribute("aria-label") or "").lower()
        # Look for inputs that already have a MM/DD/YYYY value (the date-picker
        # pre-fills them) OR have 'date'/'start'/'end' in their aria-label.
        if re.search(r"\d{2}/\d{2}/\d{4}", val) or any(
            kw in label for kw in ("date", "start", "end")
        ):
            date_inputs.append(el)
        if len(date_inputs) == 2:
            break

    if len(date_inputs) < 2:
        # Last-resort fallback: first two visible text inputs are usually it.
        date_inputs = [inputs.nth(0), inputs.nth(1)]

    date_inputs[0].fill(start_str)
    page.wait_for_timeout(300)
    date_inputs[1].fill(end_str)
    page.keyboard.press("Enter")
    page.wait_for_timeout(1_500)
    page.keyboard.press("Escape")
    page.wait_for_timeout(2_000)  # let report refetch


def _trigger_export_and_download(
    page,
    *,
    start: datetime.date,
    end: datetime.date,
    max_generate_wait_s: int = 300,
) -> pathlib.Path:
    """Click Export -> Generate -> wait for ready -> Download."""
    # Open Export panel
    page.get_by_role("button", name=re.compile(r"^Export$", re.I)).first.click()
    page.wait_for_timeout(800)

    # Click Generate
    page.get_by_role("button", name=re.compile(r"Generate Transactions CSV", re.I)).first.click()

    # Poll for the Download button to appear (replaces Generate when ready).
    download_btn_pattern = re.compile(r"Download Transactions CSV", re.I)
    deadline = time.monotonic() + max_generate_wait_s
    while time.monotonic() < deadline:
        try:
            btn = page.get_by_role("button", name=download_btn_pattern).first
            if btn.is_visible(timeout=500):
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    else:
        raise RuntimeError(
            f"Square Generate CSV did not complete within {max_generate_wait_s}s. "
            "Possible Square-side outage; will retry on next scheduled run."
        )

    # Trigger the actual download.
    rename = f"transactions-{start.isoformat()}-{(end + datetime.timedelta(days=1)).isoformat()}.csv"
    return download_to(
        page,
        trigger=lambda: page.get_by_role("button", name=download_btn_pattern).first.click(),
        rename_to=rename,
        timeout_ms=120_000,
    )


def download_transactions(
    *,
    start_date: datetime.date,
    end_date: datetime.date,
    store: str = "palmetto",
    headed: bool = True,
    slow_mo_ms: int = 50,
    keep_open_on_error: bool = False,
) -> pathlib.Path:
    """Headless-or-headed scrape of Square Transactions CSV for [start_date, end_date] inclusive.

    Returns the path to the downloaded CSV in extracted/downloads/.

    Re-entrancy: a process-level lock (`/tmp/bhaga-square-scrape.lock`) ensures
    only one Square scrape runs at a time per machine. Starting a second
    scrape while a first is in flight would fire a second 2FA SMS to the
    operator and waste their OTP reply — so the second invocation hard-fails
    immediately instead. The lock auto-reclaims if its owning PID is dead.

    Idempotency: if `extracted/downloads/transactions-{start}-{end+1}.csv`
    already exists with today's mtime (CT) and is non-empty, return it
    without launching the browser. Eliminates the duplicate-SMS storm when
    a downstream step failed and the wrapper re-fires the cron. Caller
    can force a re-download by deleting the file or with --force.
    """
    expected = DOWNLOADS_DIR / (
        f"transactions-{start_date.isoformat()}-"
        f"{(end_date + datetime.timedelta(days=1)).isoformat()}.csv"
    )
    if is_fresh_download(expected):
        print(f"[square_transactions] SKIP browser — fresh CSV already on disk: {expected}")
        return expected

    _acquire_scrape_lock(store)
    try:
        with launch_persistent(
            portal="square",
            headed=headed,
            slow_mo_ms=slow_mo_ms,
            keep_open_on_error=keep_open_on_error,
        ) as (ctx, page):
            _ensure_logged_in(page, store=store)
            page.goto(TRANSACTIONS_URL, wait_until="domcontentloaded")
            # Don't rely on networkidle (Square dashboard maintains long-polling
            # connections). Wait for the date-range pill button to render — that's
            # the cue that the transactions UI is interactive.
            page.locator("button").filter(has_text=re.compile(r"\d{2}/\d{2}/\d{4}")).first.wait_for(
                state="visible", timeout=30_000
            )
            page.wait_for_timeout(1_500)  # extra settle for the toolbar
            _set_date_range(page, start=start_date, end=end_date)
            csv_path = _trigger_export_and_download(page, start=start_date, end=end_date)
        return csv_path
    finally:
        _release_scrape_lock()


def _set_item_sales_date_range(page, *, start: datetime.date, end: datetime.date) -> None:
    """Open the item-sales date picker and type a precise start/end range.

    Square has updated the item-sales page to use the same MM/DD/YYYY format
    as the transactions page. Falls back to the old month-label format for
    backward compatibility.

    Selectors are JSON-driven (skills/square_tips/selectors/item_sales.json →
    selectors.date_picker); a Square UI drift is a one-file edit, no code change.
    """
    dp = _item_sales_selectors()["date_picker"]
    pill = _find_item_sales_pill(page)
    if pill is None:
        raise RuntimeError("Item Sales date picker pill not found")
    pill.click()
    page.wait_for_timeout(dp.get("post_open_wait_ms", 800))

    start_str = start.strftime("%m/%d/%Y")
    end_str = end.strftime("%m/%d/%Y")

    keywords = tuple(dp.get("date_input_label_keywords", ["date", "start", "end"]))
    inputs = page.locator(dp.get("date_input_selector", "input[type='text']:visible"))
    n = inputs.count()
    date_inputs = []
    for i in range(n):
        el = inputs.nth(i)
        val = el.input_value() or ""
        label = (el.get_attribute("aria-label") or "").lower()
        if re.search(r"\d{2}/\d{2}/\d{4}", val) or any(kw in label for kw in keywords):
            date_inputs.append(el)
        if len(date_inputs) == 2:
            break

    if len(date_inputs) < 2:
        date_inputs = [inputs.nth(0), inputs.nth(1)]

    date_inputs[0].fill(start_str)
    page.wait_for_timeout(300)
    date_inputs[1].fill(end_str)
    page.keyboard.press("Enter")
    page.wait_for_timeout(1_500)
    page.keyboard.press("Escape")
    page.wait_for_timeout(2_000)


def _trigger_item_sales_export(
    page,
    *,
    start: datetime.date,
    end: datetime.date,
) -> pathlib.Path:
    """Click Export -> Detail CSV Export -> wait for download.

    Unlike Transactions (async Generate -> poll -> Download), the Item Sales
    Detail CSV is a direct download triggered from the Export dropdown.

    Export-button and menu-item selectors are JSON-driven (selectors.export);
    each pattern is tried in order so a label change is a one-file edit.
    """
    ex = _item_sales_selectors()["export"]

    export_patterns = ex.get("export_button_patterns", [r"^Export$"])
    clicked = False
    for pat in export_patterns:
        try:
            page.get_by_role("button", name=re.compile(pat, re.I)).first.click()
            clicked = True
            break
        except Exception:
            continue
    if not clicked:
        raise RuntimeError(
            f"Item Sales Export button not found (patterns={export_patterns}). "
            f"Current URL: {page.url}"
        )
    page.wait_for_timeout(ex.get("menu_open_wait_ms", 800))

    detail_patterns = ex.get("detail_csv_patterns", [r"Detail\s+CSV"])

    def _click_detail_csv():
        for pat in detail_patterns:
            loc = page.get_by_text(re.compile(pat, re.I)).first
            try:
                loc.click()
                return
            except Exception:
                continue
        raise RuntimeError(
            f"Item Sales 'Detail CSV' menu item not found (patterns={detail_patterns})."
        )

    rename = f"items-{start.isoformat()}-{(end + datetime.timedelta(days=1)).isoformat()}.csv"
    return download_to(
        page,
        trigger=_click_detail_csv,
        rename_to=rename,
        timeout_ms=ex.get("download_timeout_ms", 120_000),
    )


def download_item_sales(
    page=None,
    *,
    start_date: datetime.date,
    end_date: datetime.date,
    store: str = "palmetto",
    headed: bool = True,
    slow_mo_ms: int = 50,
) -> pathlib.Path:
    """Download Item Sales Detail CSV.

    Can be called in two modes:
      1. With a ``page`` argument — reuses an already-logged-in Playwright page
         (designed for same-session use with ``download_transactions()``).
      2. Without ``page`` (page=None) — opens its own browser session, logs in,
         and downloads. Uses the same scrape lock as download_transactions.

    Returns the path to the downloaded CSV in extracted/downloads/.

    Idempotency: if the expected file already exists with today's CT mtime
    and is non-empty, returns it without touching the browser.

    Usage::

        # Shared session (preferred — single OTP):
        with launch_persistent("square") as (ctx, page):
            _ensure_logged_in(page, store="palmetto")
            item_csv = download_item_sales(page, start_date=..., end_date=...)

        # Standalone (opens own session):
        item_csv = download_item_sales(start_date=..., end_date=..., store="palmetto")
    """
    expected = DOWNLOADS_DIR / (
        f"items-{start_date.isoformat()}-"
        f"{(end_date + datetime.timedelta(days=1)).isoformat()}.csv"
    )
    if is_fresh_download(expected):
        print(f"[square_item_sales] SKIP browser — fresh CSV already on disk: {expected}")
        return expected

    if page is not None:
        return _download_item_sales_with_page(page, start_date=start_date, end_date=end_date)

    _acquire_scrape_lock(store)
    try:
        with launch_persistent(
            portal="square",
            headed=headed,
            slow_mo_ms=slow_mo_ms,
        ) as (ctx, p):
            _ensure_logged_in(p, store=store)
            return _download_item_sales_with_page(p, start_date=start_date, end_date=end_date)
    finally:
        _release_scrape_lock()


def _download_item_sales_with_page(
    page,
    *,
    start_date: datetime.date,
    end_date: datetime.date,
) -> pathlib.Path:
    """Internal: navigate to item-sales report and export the Detail CSV.

    The date-picker pill is detected via JSON-driven, ordered fallbacks
    (selectors/item_sales.json → selectors.date_picker). Square has drifted this
    control before (month-label → MM/DD/YYYY → the 2026-05-31 'not found'
    incident); absorbing the next drift is a one-file selector edit.
    """
    page.goto(ITEM_SALES_URL, wait_until="domcontentloaded")
    if _find_item_sales_pill(page) is None:
        raise RuntimeError(
            f"Item Sales page date picker not found within timeout. "
            f"Tried patterns + structural locators from "
            f"selectors/item_sales.json (selectors.date_picker). "
            f"Current URL: {page.url}"
        )
    page.wait_for_timeout(1_500)

    _set_item_sales_date_range(page, start=start_date, end=end_date)
    return _trigger_item_sales_export(page, start=start_date, end=end_date)


def _kds_navigate_calendar_to_month(page, *, target_year: int, target_month: int) -> None:
    """If the KDS calendar is showing a different month, click the back arrow until we reach it."""
    import calendar
    month_names = {i: calendar.month_name[i] for i in range(1, 13)}
    target_label = f"{month_names[target_month]} {target_year}"

    for _ in range(12):
        header = page.locator("[class*='calendar'] h2, [class*='calendar'] [class*='header'], [class*='month-label']").first
        try:
            text = header.text_content(timeout=3_000) or ""
        except Exception:
            break
        if target_label.lower() in text.lower():
            return
        # Click the back/previous arrow
        back_btn = page.locator(
            "button[aria-label*='previous'], button[aria-label*='Previous'], "
            "button[aria-label*='back'], [class*='calendar'] button:first-child"
        ).first
        try:
            back_btn.click(timeout=3_000)
            page.wait_for_timeout(500)
        except Exception:
            break


def _kds_set_date_range(page, *, start: datetime.date, end: datetime.date) -> None:
    """Open the KDS date picker and type start/end dates into the input fields.

    Typing into the Start / End text inputs avoids month-by-month calendar
    navigation, which breaks when the target date is more than one month back.
    """
    date_trigger = page.locator("[data-test-sq-date-filter-dropdown-trigger]")
    date_trigger.wait_for(state="visible", timeout=15_000)
    date_trigger.click()
    page.wait_for_timeout(1_000)

    start_str = start.strftime("%m/%d/%Y")
    end_str = end.strftime("%m/%d/%Y")

    start_input = page.locator(".begin-date input.input-date")
    start_input.wait_for(state="visible", timeout=5_000)
    start_input.click(click_count=3)
    page.wait_for_timeout(200)
    start_input.fill(start_str)
    page.wait_for_timeout(500)
    page.keyboard.press("Tab")
    page.wait_for_timeout(500)

    end_input = page.locator(".end-date input.input-date")
    end_input.wait_for(state="visible", timeout=5_000)
    end_input.click(click_count=3)
    page.wait_for_timeout(200)
    end_input.fill(end_str)
    page.wait_for_timeout(500)
    page.keyboard.press("Enter")
    page.wait_for_timeout(1_000)


def _kds_trigger_export_and_download(
    page,
    *,
    start: datetime.date,
    end: datetime.date,
    max_generate_wait_s: int = 300,
) -> pathlib.Path:
    """Click Export -> Generate -> wait -> Download for KDS report."""
    # Click the Export button
    export_btn = page.locator("market-button[aria-label='Export'], button[aria-label='Export']").first
    export_btn.wait_for(state="visible", timeout=10_000)
    export_btn.click()
    page.wait_for_timeout(1_000)

    # Click Generate
    gen_btn = page.get_by_role("button", name=re.compile(r"Generate", re.I)).first
    gen_btn.wait_for(state="visible", timeout=10_000)
    gen_btn.click()

    # Wait for Download button to appear
    deadline = time.monotonic() + max_generate_wait_s
    while time.monotonic() < deadline:
        try:
            dl_btn = page.locator(".async-report-export-popover__button").first
            if dl_btn.is_visible(timeout=500):
                break
        except Exception:
            pass
        # Also try by role as fallback
        try:
            dl_btn = page.get_by_role("button", name=re.compile(r"Download", re.I)).first
            if dl_btn.is_visible(timeout=500):
                break
        except Exception:
            pass
        time.sleep(2)
    else:
        raise RuntimeError(
            f"KDS Generate CSV did not complete within {max_generate_wait_s}s."
        )

    rename = f"kds-{start.isoformat()}-{(end + datetime.timedelta(days=1)).isoformat()}.csv"
    return download_to(
        page,
        trigger=lambda: page.locator(
            ".async-report-export-popover__button, "
            "button:has-text('Download')"
        ).first.click(),
        rename_to=rename,
        timeout_ms=120_000,
    )


def download_kds_report(
    page=None,
    *,
    start_date: datetime.date,
    end_date: datetime.date,
    store: str = "palmetto",
    headed: bool = True,
    slow_mo_ms: int = 50,
) -> pathlib.Path:
    """Download the KDS Performance Report CSV for [start_date, end_date] inclusive.

    Can be called in two modes:
      1. With a ``page`` argument — reuses an already-logged-in Playwright page
         (same-session use alongside transactions + item-sales).
      2. Without ``page`` (page=None) — opens its own browser session and logs in.

    Returns the path to the downloaded CSV in extracted/downloads/.

    The KDS report is at /dashboard/kitchen/reports/performance. Flow:
      1. Navigate to KDS page
      2. Click date text to open calendar picker
      3. Select start date via [data-test-calendar-month-day='M/D']
      4. Select end date via same selector
      5. Click "Run report"
      6. Wait for table to load
      7. Click Export -> Generate -> Download
    """
    expected = DOWNLOADS_DIR / (
        f"kds-{start_date.isoformat()}-"
        f"{(end_date + datetime.timedelta(days=1)).isoformat()}.csv"
    )
    if is_fresh_download(expected):
        print(f"[square_kds] SKIP browser — fresh CSV already on disk: {expected}")
        return expected

    if page is not None:
        return _download_kds_with_page(page, start_date=start_date, end_date=end_date)

    _acquire_scrape_lock(store)
    try:
        with launch_persistent(
            portal="square", headed=headed, slow_mo_ms=slow_mo_ms,
        ) as (ctx, p):
            _ensure_logged_in(p, store=store)
            return _download_kds_with_page(p, start_date=start_date, end_date=end_date)
    finally:
        _release_scrape_lock()


def _download_kds_with_page(
    page,
    *,
    start_date: datetime.date,
    end_date: datetime.date,
) -> pathlib.Path:
    """Internal: navigate to KDS performance report and export CSV."""
    page.goto(KDS_PERFORMANCE_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3_000)

    # Set the date range via calendar picker
    _kds_set_date_range(page, start=start_date, end=end_date)

    # Click "Run report"
    run_btn = page.get_by_role("button", name=re.compile(r"Run\s+report", re.I)).first
    run_btn.wait_for(state="visible", timeout=10_000)
    run_btn.click()

    # Wait for the report table to load (look for table content or a known column header)
    page.wait_for_timeout(3_000)
    try:
        page.locator("table, [class*='report-table'], [class*='data-table']").first.wait_for(
            state="visible", timeout=30_000,
        )
    except Exception:
        # Fallback: wait for Export button to become enabled (signals report ready)
        page.locator(
            "market-button[aria-label='Export'], button[aria-label='Export']"
        ).first.wait_for(state="visible", timeout=30_000)
    page.wait_for_timeout(1_500)

    return _kds_trigger_export_and_download(page, start=start_date, end=end_date)


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--start", required=True, help="YYYY-MM-DD start date (inclusive).")
    cli.add_argument("--end", required=True, help="YYYY-MM-DD end date (inclusive).")
    cli.add_argument("--store", default="palmetto")
    cli.add_argument("--headless", action="store_true",
                     help="Run without a visible browser window (faster, anti-bot risk).")
    cli.add_argument("--keep-open", action="store_true",
                     help="On error, leave the browser open for manual inspection.")
    args = cli.parse_args()

    start = datetime.date.fromisoformat(args.start)
    end = datetime.date.fromisoformat(args.end)
    print(f"# Square Transactions scrape: {start} → {end} (store={args.store})")

    csv_path = download_transactions(
        start_date=start, end_date=end,
        store=args.store,
        headed=not args.headless,
        keep_open_on_error=args.keep_open,
    )
    print(f"# Downloaded: {csv_path}")

    # Quick sanity check: parse the CSV and report counts.
    records = parse_csv(csv_path)
    n = len(records)
    tip_total_cents = sum(r["tip_cents"] for r in records)
    sales_total_cents = sum(r["gross_sales_cents"] for r in records)
    print(f"# Parsed {n} transactions; tips=${tip_total_cents/100:.2f}; sales=${sales_total_cents/100:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
