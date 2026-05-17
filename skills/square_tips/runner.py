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
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from skills._browser_runtime.runtime import (
    DOWNLOADS_DIR,
    download_to,
    launch_persistent,
)
from skills.square_tips.transactions_backend import parse_csv, get_credentials

LOGIN_URL = "https://app.squareup.com/login"
TRANSACTIONS_URL = "https://app.squareup.com/dashboard/sales/transactions"


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

    print(f"[square 2fa] requesting OTP via Slack for store={store!r}...")
    code = request_otp(
        user_id="U0APJRE5DC4",          # operator (primary_user_id from config.yaml)
        portal_name="Square",
        timeout_seconds=1800,             # 30 min — operator may be away from phone
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
    """
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
