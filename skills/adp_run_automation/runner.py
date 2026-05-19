"""Standalone Playwright runner for ADP RUN report downloads.

Two scrapes:
    download_timecard(...)  — Reports > Time reports > Timecard, exports .xlsx
                              of all employee punches across selected pay periods.
                              Run nightly.
    download_earnings(...)  — Reports > My saved custom reports > "Earnings and
                              Hours V1", exports .xlsx with wage rates +
                              Credit-Card-Tips-Owed lines per paycheck.
                              Only needed every 14 days (after each pay-period
                              close), but cheap to run nightly.

ADP quirks accounted for:
    - Timecard report runs INSIDE iframe[name="mdfTimeFrame"] -- all selectors
      must traverse the iframe.
    - Export-to-Excel is a custom <SDF-BUTTON> with id "report-excel-button";
      role-based selectors don't always find it, so we fall back to JS click.
    - Earnings report uses an async generation flow: Download dropdown -> Excel
      menu item -> wait for "Your report is ready" dialog -> click Download report.
    - Login may or may not be prompted: cached session lands directly on dashboard;
      expired session shows a password prompt (no username; ADP remembers it).
    - If MFA is challenged, we ALERT-AND-EXIT rather than try to drive it (TODO
      M3.10: wire up skills/slack.request_otp for OTP delivery).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import time
from typing import Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from skills._browser_runtime.runtime import (
    DOWNLOADS_DIR,
    download_to,
    is_fresh_download,
    launch_persistent,
)

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
STORE_PROFILES = PROJECT_ROOT / "agents" / "bhaga" / "knowledge-base" / "store-profiles"

LOGIN_URL = "https://runpayroll.adp.com"
POST_LOGIN_URL_RE = re.compile(r"runpayrollmain\.adp\.com/.*/v2/")
KEYCHAIN_SERVICE_TEMPLATE = "jarvis-adp-{store}"


# ── Credentials ────────────────────────────────────────────────────


def _get_adp_password(store: str) -> str:
    """Pull ADP password from macOS Keychain via `security find-generic-password`."""
    import subprocess
    svc = KEYCHAIN_SERVICE_TEMPLATE.format(store=store)
    result = subprocess.run(
        ["security", "find-generic-password", "-s", svc, "-w"],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ADP password not in Keychain (service={svc!r}). "
            f"Add with: security add-generic-password -s {svc} -a {{username}} -w {{password}}"
        )
    return result.stdout.strip()


# ── Login (shared by both scrapes) ─────────────────────────────────


def _get_adp_username(store: str) -> str:
    """Read the ADP User ID stored alongside the password in Keychain.

    The keychain entry's `acct` attribute holds the user-visible User ID
    (e.g. an email). Same entry used by _get_adp_password() — single source
    of truth so password rotations don't require touching the username.
    """
    svc = KEYCHAIN_SERVICE_TEMPLATE.format(store=store)
    result = subprocess.run(
        ["security", "find-generic-password", "-s", svc, "-g"],
        capture_output=True, text=True, timeout=5,
    )
    # `security -g` prints account metadata to stderr in the form `acct"<blob>="value"`.
    out = result.stderr + result.stdout
    for line in out.splitlines():
        if '"acct"<blob>=' in line:
            # Format: `    "acct"<blob>="aditya.2ky@gmail.com"`
            return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError(
        f"Could not parse account from Keychain entry {svc!r}. "
        f"Set it with: security add-generic-password -U -s {svc} -a {{username}} -w {{password}}"
    )


def _ensure_logged_in(page, *, store: str, timeout_ms: int = 30_000) -> None:
    """Open ADP and complete a FRESH login using keychain creds.

    Stateless: assumes no cookies (ephemeral browser context). Flow:
        1. Navigate to LOGIN_URL.
        2. If we land directly on the dashboard (rare; only if the IP happens
           to have a residual ADP cookie), return.
        3. Fill User ID, click Next.
        4. Fill Password, click Sign in.
        5. Wait for the dashboard URL.

    Raises with detailed context on:
        - 2FA / verification challenges (Slack-DM operator before raising)
        - "User ID and/or password incorrect" — keychain creds need rotation
        - Any unexpected post-login URL
    """
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:  # noqa: BLE001
        pass

    if POST_LOGIN_URL_RE.search(page.url):
        return  # Already authenticated (shouldn't happen in ephemeral, but defensive).

    # Step 1: User ID. sdf-input wraps a real <input>; get_by_role finds it.
    uid_box = page.get_by_role("textbox", name=re.compile(r"^User ID$", re.I)).first
    uid_box.wait_for(state="visible", timeout=10_000)
    uid_box.fill(_get_adp_username(store))
    page.get_by_role("button", name=re.compile(r"^Next$", re.I)).first.click()

    # Step 2: Password. ADP enables the button only after the password field
    # is populated, so just press Enter rather than racing the disabled state.
    pw_box = page.get_by_role("textbox", name=re.compile(r"^Password$", re.I)).first
    try:
        pw_box.wait_for(state="visible", timeout=10_000)
    except Exception:
        # Could be MFA / device-trust challenge inserted between User ID and Password.
        _raise_with_evidence(
            page, store=store,
            reason="ADP did not show the Password field after User ID step. "
                   "Likely a 2FA / device-trust challenge that isn't wired yet. "
                   "Screenshot saved for diagnosis.",
        )
    pw_box.fill(_get_adp_password(store))
    page.keyboard.press("Enter")

    # Step 3: Land on dashboard. ADP may insert a verification step for 2FA,
    # or surface "User ID/password incorrect" inline.
    try:
        page.wait_for_url(POST_LOGIN_URL_RE, timeout=timeout_ms)
    except Exception:
        # Diagnose: incorrect creds, 2FA, or unexpected redirect.
        url = page.url.lower()
        on_2fa_url = any(t in url for t in ("verify", "challenge", "step-up", "mfa"))
        # Fallback: ADP sometimes serves 2FA on the bare login origin without a
        # URL marker — check the visible body for the "Verify your identity"
        # headline before deciding it's not 2FA.
        on_2fa_body = False
        if not on_2fa_url:
            try:
                on_2fa_body = page.get_by_text(
                    re.compile(r"verify your identity|verification code|text message", re.I)
                ).first.is_visible(timeout=1_000)
            except Exception:  # noqa: BLE001
                pass
        if on_2fa_url or on_2fa_body:
            print(f"[adp 2fa] detected challenge at {page.url}; engaging Slack-OTP handler...")
            _handle_adp_two_factor(page, store=store)
            # Handler returned: re-confirm we reached the dashboard.
            page.wait_for_url(POST_LOGIN_URL_RE, timeout=timeout_ms)
            return
        # Check for the inline "incorrect credentials" banner.
        try:
            banner = page.get_by_text(re.compile(r"user ID and/or password are incorrect", re.I)).first
            if banner.is_visible(timeout=2_000):
                _raise_with_evidence(
                    page, store=store,
                    reason=f"ADP rejected creds for {_get_adp_username(store)!r}. "
                           f"Keychain password is stale — rotate with: "
                           f"security add-generic-password -U -s jarvis-adp-{store} -a <user> -w <new-password>",
                )
        except Exception:  # noqa: BLE001
            pass
        _raise_with_evidence(
            page, store=store,
            reason=f"ADP login did not reach dashboard. Current URL: {page.url}",
        )


def _handle_adp_two_factor(page, *, store: str) -> None:
    """Drive ADP RUN's SMS-OTP 2FA flow with operator-in-the-loop via Slack.

    Mirrors skills/square_tips/runner.py::_handle_square_two_factor. ADP's
    challenge page is built with custom <sdf-*> Web Components ("Secure
    Design Framework") and tends to drift in attribute structure, so this
    code prefers role/text matching over sdf-tag CSS selectors.

    Flow:
        1. Pick the "Text message" delivery option (radio or button).
        2. Click Continue/Send -> ADP sends SMS to the phone on file
           (per operator policy: number ending 0038 is first preference).
        3. Wait for the 6-digit code input on the next screen.
        4. Call skills.slack.adapter.request_otp(agent="bhaga") -> blocks
           up to 30 min waiting for the operator to reply with the code
           in the BHAGA DM (Socket-Mode listener picks it up).
        5. Fill the code, submit (Enter / Verify / Continue).

    Raises with screenshot+html evidence if any step's selector chain fails
    so we don't end up in a silent infinite wait.
    """
    print(f"[adp 2fa] starting handler; URL={page.url}")

    # Step 1: pick text-message delivery. ADP's picker varies between sdf-radio
    # and clickable sdf-card. Try several patterns.
    sms_picked = False
    for selector_fn in (
        lambda: page.get_by_role("radio", name=re.compile(r"text\s*message|SMS|text\b", re.I)).first,
        lambda: page.get_by_role("button", name=re.compile(r"text\s*message|SMS", re.I)).first,
        lambda: page.get_by_text(re.compile(r"text\s*message", re.I)).first,
    ):
        try:
            loc = selector_fn()
            loc.wait_for(state="visible", timeout=4_000)
            try:
                loc.check()
            except Exception:  # noqa: BLE001 — non-radio elements need click
                loc.click()
            sms_picked = True
            break
        except Exception:  # noqa: BLE001
            continue
    if not sms_picked:
        # ADP may have already moved straight to the code-entry screen (some
        # accounts skip the delivery picker). Don't fail — proceed and let
        # the code-input wait below confirm where we are.
        print("[adp 2fa] no delivery picker found; assuming code-entry screen already shown")

    # Step 2: click Continue / Send / Next to trigger SMS send. Skip if we
    # never found a picker — we're already on the code screen.
    if sms_picked:
        try:
            page.get_by_role(
                "button", name=re.compile(r"^continue$|^next$|^send$|^send code$", re.I)
            ).first.click(timeout=4_000)
        except Exception:  # noqa: BLE001
            try:
                page.keyboard.press("Enter")
            except Exception:  # noqa: BLE001
                pass

    # Step 3: wait for the code-entry input. ADP uses either a single text
    # input or a 6-digit-box widget. Cover both.
    page.wait_for_timeout(2_500)  # let the next screen render
    code_input = None
    for css in [
        "input[autocomplete='one-time-code']",
        "input[name='code']",
        "input[name='otp']",
        "input[inputmode='numeric']",
        "input[type='text'][maxlength='6']",
        "input[type='tel'][maxlength='6']",
    ]:
        try:
            loc = page.locator(css).first
            loc.wait_for(state="visible", timeout=4_000)
            code_input = loc
            break
        except Exception:  # noqa: BLE001
            continue

    six_digit_boxes = None
    if code_input is None:
        # Try the per-digit input widget.
        digit_inputs = page.locator("input[type='text'][maxlength='1'], input[type='tel'][maxlength='1']")
        try:
            count = digit_inputs.count()
        except Exception:  # noqa: BLE001
            count = 0
        if count >= 4:
            six_digit_boxes = digit_inputs
        else:
            _raise_with_evidence(
                page, store=store,
                reason=f"ADP 2FA code-entry input not found. Selector drift. "
                       f"Current URL: {page.url}",
            )

    # Step 4: request OTP via Slack DM, block until operator replies.
    from skills.slack.adapter import request_otp  # local import: optional dep

    print(f"[adp 2fa] requesting OTP via Slack for store={store!r}; SMS expected at +1-XXX-XXX-0038")
    code = request_otp(
        user_id="U0APJRE5DC4",       # operator (primary_user_id from config.yaml)
        portal_name="ADP",
        timeout_seconds=1800,         # 30 min — operator may be away from phone
        phone_hint="+1-XXX-XXX-0038",
        agent="bhaga",
    )
    if not code:
        raise RuntimeError(
            "ADP 2FA: operator did not reply with the OTP within 30 minutes. "
            "Either retry the scrape (a new SMS will fire) or complete login manually."
        )
    code = code.strip().replace(" ", "").replace("-", "")
    print(f"[adp 2fa] got code (len={len(code)}); submitting.")

    # Step 5: fill the code and submit.
    if code_input is not None:
        code_input.fill(code)
    else:
        for i, ch in enumerate(code):
            six_digit_boxes.nth(i).fill(ch)

    try:
        page.get_by_role(
            "button", name=re.compile(r"^verify$|^continue$|^submit$|^next$|^sign in$", re.I)
        ).first.click(timeout=4_000)
    except Exception:  # noqa: BLE001
        page.keyboard.press("Enter")

    # Brief wait so the post-submit nav has a chance to start before the
    # caller's wait_for_url runs (avoids racing on stale `page.url`).
    page.wait_for_timeout(1_500)


def _mark_run_step_done(step_name: str, *, note: str = "") -> None:
    """Best-effort write of ~/.bhaga/state/run-<today_ct>/{step_name}.done.

    Mirrors agents.bhaga.scripts.daily_refresh.mark_step_done so the bundle
    helper can record per-component completion (adp_timecard, adp_earnings)
    even though the orchestrator's run_step only sees the bundle-level call
    (adp_reports). Preserves per-component granularity for the wrapper
    roll-up alert and operator-facing debugging.
    """
    try:
        from zoneinfo import ZoneInfo
        ct = ZoneInfo("America/Chicago")
        today_ct = datetime.datetime.now(ct).date()
        d = pathlib.Path.home() / ".bhaga" / "state" / f"run-{today_ct.isoformat()}"
        d.mkdir(parents=True, exist_ok=True)
        body = datetime.datetime.now(ct).isoformat()
        if note:
            body += f"\nnote: {note}"
        (d / f"{step_name}.done").write_text(body)
    except Exception as exc:  # noqa: BLE001
        print(f"[adp_bundle] WARN: could not write {step_name} marker: {exc}")


def _raise_with_evidence(page, *, store: str, reason: str) -> None:
    """Save screenshot + URL alongside the raise so failures are debuggable.

    The standard _browser_runtime evidence capture also fires when the
    exception propagates out of the launch_persistent with-block, so we
    end up with two snapshots — the one taken here is at the exact moment
    of the auth failure (most useful), the other is at context teardown.
    """
    try:
        ts = subprocess.run(["date", "+%Y%m%d-%H%M%S"], capture_output=True, text=True).stdout.strip()
        snap = f"/Users/adityaparikh/.bhaga/state/screenshots/adp-{store}-authfail-{ts}.png"
        page.screenshot(path=snap, full_page=True)
        reason += f"\nScreenshot: {snap}"
    except Exception:  # noqa: BLE001
        pass
    raise RuntimeError(reason)


# ── Timecard scrape ────────────────────────────────────────────────


def download_timecard(
    *,
    store: str = "palmetto",
    target_date: Optional[datetime.date] = None,
    headed: bool = True,
    slow_mo_ms: int = 50,
    keep_open_on_error: bool = False,
) -> pathlib.Path:
    """Open Reports > Time reports > Timecard, select pay periods, apply
    changes, click Export to Excel, save .xlsx.

    Args:
        target_date: if provided, only the single pay period whose date range
            contains this date is selected (nightly-incremental mode). If
            None, ALL pay periods are selected (backfill mode — backwards
            compatible with the original behavior).

    Idempotency: if today's Timecard XLSX is already on disk (CT-today mtime),
    skip the browser entirely and return the cached path. Eliminates
    duplicate ADP 2FA SMS on cron retries.
    """
    expected = DOWNLOADS_DIR / f"Timecard-{datetime.date.today().isoformat()}.xlsx"
    if is_fresh_download(expected, min_bytes=10_000):
        print(f"[adp_timecard] SKIP browser — fresh Timecard XLSX already on disk: {expected}")
        return expected

    with launch_persistent(
        portal="adp",
        headed=headed,
        slow_mo_ms=slow_mo_ms,
        keep_open_on_error=keep_open_on_error,
    ) as (ctx, page):
        _ensure_logged_in(page, store=store)
        return _timecard_within_session(page, target_date=target_date)


def _timecard_within_session(
    page, *, target_date: Optional[datetime.date] = None
) -> pathlib.Path:
    """Run the Timecard scrape on an already-authenticated ADP dashboard page.

    Extracted from download_timecard so the bundle helper can drive both
    Timecard and Earnings within a single browser session (one login, one
    OTP cost). Caller owns the browser context lifecycle.

    Pre-condition: `page` is on the v2 ADP RUN dashboard (POST_LOGIN_URL_RE).
    """
    # Navigate to Timecard: Reports-btn → Reports landing → "View all
    # reports" opens the Single Reports modal → expand "Time reports"
    # accordion → click "Timecard". ADP dashboard maintains persistent
    # connections so 'networkidle' never fires; use targeted waits.
    page.locator('[data-test-id="Reports-btn"]').first.click()
    # After Reports-btn click ADP can land on either:
    #   (a) the Reports landing page (test-id="view-all-reports") OR
    #   (b) the homepage Reports widget (test-id="reports-tile-view-all-reports-button")
    view_all = page.locator(
        '[data-test-id="view-all-reports"], [data-test-id="reports-tile-view-all-reports-button"]'
    ).first
    view_all.wait_for(state="visible", timeout=20_000)
    view_all.scroll_into_view_if_needed(timeout=5_000)
    view_all.click()
    # Single Reports modal: ensure the "Time" section header is loaded, then
    # click the Timecard report tile. The section accordion is often already
    # expanded by default; if not, clicking the header expands it.
    time_section_header = page.locator('[data-test-id="Time-section_Head_HeaderLabel"]').first
    time_section_header.wait_for(state="visible", timeout=15_000)
    timecard_tile = page.locator('[data-test-id="Time-tile-list-item-Timecard"]').first
    try:
        timecard_tile.wait_for(state="visible", timeout=3_000)
    except Exception:
        time_section_header.click()
        page.wait_for_timeout(500)
        timecard_tile.wait_for(state="visible", timeout=10_000)
    timecard_tile.scroll_into_view_if_needed(timeout=5_000)
    timecard_tile.click()
    # Timecard report iframe rendering — wait for the iframe directly.
    page.locator("iframe[name='mdfTimeFrame']").wait_for(state="attached", timeout=20_000)
    page.wait_for_timeout(2_500)

    # All Timecard report controls live inside iframe[name="mdfTimeFrame"].
    page.wait_for_timeout(4_000)
    frame = page.frame_locator("iframe[name='mdfTimeFrame']")

    # Pay Period selection. There are TWO comboboxes with "Pay Period" in
    # their name — "Report Period" (single-select; its current value is
    # "Pay P..." which a permissive regex matches) and the actual "Pay Period"
    # multi-select. Anchor on exact name to disambiguate.
    # Accessible name format on these sdf-inputs is "{Label} {Value}", e.g.
    # "Report Period Pay P..." or "Pay Period 1 Selected". Anchor START so we
    # don't accidentally match Report Period (whose value contains "Pay P").
    pp_combobox = frame.get_by_role(
        "combobox", name=re.compile(r"^Pay Period\b", re.I)
    ).first
    pp_combobox.click()
    page.wait_for_timeout(800)

    if target_date is not None:
        # Nightly-incremental mode: pick exactly the one pay period whose
        # date range contains target_date. Cuts XLSX size by ~26x and skips
        # re-pulling already-mirrored periods.
        date_range_re = re.compile(
            r"(\d{1,2})/(\d{1,2})/(\d{4})\s*-\s*(\d{1,2})/(\d{1,2})/(\d{4})"
        )
        opts = frame.get_by_role("option", name=date_range_re)
        try:
            n = opts.count()
        except Exception:  # noqa: BLE001
            n = 0
        matched = False
        for i in range(n):
            opt = opts.nth(i)
            try:
                name = opt.get_attribute("aria-label") or opt.inner_text()
            except Exception:  # noqa: BLE001
                name = ""
            m = date_range_re.search(name or "")
            if not m:
                continue
            start = datetime.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
            end = datetime.date(int(m.group(6)), int(m.group(4)), int(m.group(5)))
            if start <= target_date <= end:
                opt.click()
                matched = True
                print(f"[adp_timecard] selected pay period "
                      f"{start.isoformat()} → {end.isoformat()} "
                      f"(contains target_date={target_date.isoformat()})")
                break
        if not matched:
            # No pay period covers target_date — likely a calibration drift.
            # Fall back to Select All so we still ship a useful XLSX rather
            # than an empty one.
            print(f"[adp_timecard] WARN: no pay period contains "
                  f"{target_date.isoformat()}; falling back to Select All")
            try:
                frame.get_by_role(
                    "option", name=re.compile(r"^Select All$", re.I)
                ).first.click()
            except Exception:  # noqa: BLE001
                pass
    else:
        # Backfill / default mode: select every pay period exposed in the
        # dropdown. The multi-select listbox has a "Select All" checkbox at
        # the top; fall back to per-option clicks if not exposed.
        try:
            select_all = frame.get_by_role(
                "option", name=re.compile(r"^Select All$", re.I)
            ).first
            select_all.wait_for(state="visible", timeout=5_000)
            select_all.click()
        except Exception:
            opts = frame.get_by_role(
                "option", name=re.compile(r"\d{1,2}/\d{1,2}/\d{4}\s*-\s*\d{1,2}/\d{1,2}/\d{4}", re.I)
            )
            n = opts.count()
            for i in range(n):
                opts.nth(i).click()
    page.wait_for_timeout(500)
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    # Format: must be "Continuous" — "Page Layout" only exposes Print (PDF)
    # and hides the Excel export button entirely.
    fmt_combobox = frame.get_by_role(
        "combobox", name=re.compile(r"^Format\b", re.I)
    ).first
    fmt_combobox.click()
    page.wait_for_timeout(500)
    try:
        frame.get_by_role(
            "option", name=re.compile(r"^Continuous$", re.I)
        ).first.click()
    except Exception:
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
    page.wait_for_timeout(400)

    frame.get_by_role("button", name=re.compile(r"^Apply Changes$", re.I)).first.click()
    page.wait_for_timeout(8_000)

    # Export To Excel — the SDF-BUTTON custom element.
    excel_btn_selector = "#report-excel-button"
    try:
        frame.locator(excel_btn_selector).first.wait_for(state="visible", timeout=10_000)
    except Exception:
        page.wait_for_timeout(5_000)
        frame.locator(excel_btn_selector).first.wait_for(state="visible", timeout=10_000)

    rename = f"Timecard-{datetime.date.today().isoformat()}.xlsx"
    path = download_to(
        page,
        trigger=lambda: frame.locator(excel_btn_selector).first.click(),
        rename_to=rename,
        timeout_ms=60_000,
    )
    return path


# ── Earnings scrape ────────────────────────────────────────────────


def _load_store_profile(store: str) -> dict:
    return json.loads((STORE_PROFILES / f"{store}.json").read_text())


def download_earnings(
    *,
    store: str = "palmetto",
    start_date: Optional[datetime.date] = None,
    end_date: Optional[datetime.date] = None,
    headed: bool = True,
    slow_mo_ms: int = 50,
    keep_open_on_error: bool = False,
) -> pathlib.Path:
    """Open Reports > My saved custom reports > '{wage_rate_report_name}',
    set Custom date range, preview, download Excel.

    Default date range: last 90 days (more than enough to infer current rates
    AND capture the most recent pay-period's Credit Card Tips Owed).

    Idempotency: if today's Earnings-and-Hours XLSX is already on disk
    (CT-today mtime), skip the browser entirely and return the cached path.
    """
    today = datetime.date.today()
    expected = DOWNLOADS_DIR / f"Earnings-and-Hours-V1-{today.isoformat()}.xlsx"
    if is_fresh_download(expected, min_bytes=5_000):
        print(f"[adp_earnings] SKIP browser — fresh Earnings XLSX already on disk: {expected}")
        return expected

    profile = _load_store_profile(store)
    report_name = profile["adp_run"].get("wage_rate_report_name", "Earnings and Hours V1")
    start = start_date or (today - datetime.timedelta(days=90))
    end = end_date or today

    with launch_persistent(
        portal="adp",
        headed=headed,
        slow_mo_ms=slow_mo_ms,
        keep_open_on_error=keep_open_on_error,
    ) as (ctx, page):
        _ensure_logged_in(page, store=store)
        return _earnings_within_session(
            page, store=store, start=start, end=end
        )


def _earnings_within_session(
    page,
    *,
    store: str,
    start: datetime.date,
    end: datetime.date,
) -> pathlib.Path:
    """Run the Earnings & Hours scrape on an already-authenticated page.

    Pre-condition: `page` is on the v2 ADP RUN dashboard. Caller owns the
    browser context.

    Selector strategy (changed 2026-05-19): the original implementation used
    `page.wait_for_load_state("networkidle", timeout=15_000)` after the
    Reports-btn click and again after the report-link click. ADP's dashboard
    holds long-poll connections open indefinitely, so networkidle NEVER
    fires and the call always hits the 15s timeout. download_timecard had
    the same bug and was fixed earlier — this helper now mirrors that
    pattern (wait for the next-step landmark element directly instead of
    a global load-state).
    """
    profile = _load_store_profile(store)
    report_name = profile["adp_run"].get("wage_rate_report_name", "Earnings and Hours V1")

    page.locator('[data-test-id="Reports-btn"]').first.click()

    # Wait for the saved-report link itself to appear on the Reports landing
    # page — it's a stable landmark and avoids networkidle's long-poll trap.
    report_link = page.get_by_role(
        "link", name=re.compile(rf"^{re.escape(report_name)}$", re.I)
    ).first
    report_link.wait_for(state="visible", timeout=20_000)
    report_link.scroll_into_view_if_needed(timeout=5_000)
    report_link.click()

    # Custom report builder opens (NO iframe). Wait for the Date range
    # combobox to be visible — that's the first control we need to drive.
    date_range_combo = page.get_by_role(
        "combobox", name=re.compile(r"Date range", re.I)
    ).first
    date_range_combo.wait_for(state="visible", timeout=20_000)
    date_range_combo.click()
    page.wait_for_timeout(300)
    page.get_by_role("option", name=re.compile(r"Custom date range", re.I)).first.click()
    page.wait_for_timeout(500)

    page.get_by_role("textbox", name=re.compile(r"From", re.I)).first.fill(start.strftime("%m/%d/%Y"))
    page.get_by_role("textbox", name=re.compile(r"To", re.I)).first.fill(end.strftime("%m/%d/%Y"))
    page.keyboard.press("Tab")
    page.wait_for_timeout(500)

    page.locator("[data-test-id='view-custom-report']").first.click()
    page.wait_for_timeout(4_000)

    page.get_by_role("button", name=re.compile(r"^Download$", re.I)).first.click()
    page.wait_for_timeout(500)
    page.get_by_role("menuitem", name=re.compile(r"Excel \(\.xlsx\)", re.I)).first.click()

    # "Your report is ready to download" dialog (~3-10s).
    ready_dialog_btn = page.locator("[data-test-id='download-report']").first
    ready_dialog_btn.wait_for(state="visible", timeout=30_000)

    rename = f"Earnings-and-Hours-V1-{datetime.date.today().isoformat()}.xlsx"
    path = download_to(
        page,
        trigger=lambda: ready_dialog_btn.click(),
        rename_to=rename,
        timeout_ms=60_000,
    )
    return path


# ── Bundle: one browser session, one login, both scrapes ───────────


def download_adp_bundle(
    *,
    store: str = "palmetto",
    target_date: Optional[datetime.date] = None,
    include_earnings: bool = True,
    earnings_window_days: int = 90,
    headed: bool = True,
    slow_mo_ms: int = 50,
    keep_open_on_error: bool = False,
) -> dict:
    """Run BOTH ADP scrapes in a single browser session.

    Motivation: the standalone `download_timecard` and `download_earnings`
    functions each open their own context → each runs `_ensure_logged_in`
    → each can trigger ADP 2FA → operator gets TWO SMS OTPs per nightly
    run. This bundle opens ONE context, logs in ONCE, and runs both
    scrapes within that session, so the operator pays at most one OTP
    cost per refresh.

    Layered idempotency:
        - Layer A (file on disk): if today's XLSX is already on disk for a
          given component, that component is skipped and the cached path
          is returned without launching the browser.
        - Layer B (per-component markers): success of each scrape writes
          `~/.bhaga/state/run-<today_ct>/{adp_timecard,adp_earnings}.done`
          so the orchestrator's per-step granularity is preserved even
          though it now invokes a single `adp_reports` step.

    Partial-success contract: if Timecard succeeds but Earnings fails (or
    vice versa), we DO NOT raise mid-flight. Both attempts run, the
    successful XLSX is returned in the result dict, and the caller can
    decide how to handle the partial failure (the orchestrator wraps this
    by raising AFTER inspecting `errors`, so the partial success is
    captured on disk + in markers before the exception propagates).

    Args:
        store: store profile name (Keychain entry key).
        target_date: date contained by the Timecard pay period to scrape.
            Passed through to `_timecard_within_session`. None = backfill.
        include_earnings: if False, only Timecard runs (orchestrator sets
            this off Mon/Tue per `_should_run_rates`).
        earnings_window_days: how far back the earnings scrape's "From"
            date should go (default 90 — enough for current rates + the
            most recent pay period's Credit Card Tips Owed).

    Returns:
        {
            "timecard_xlsx": pathlib.Path | None,
            "earnings_xlsx": pathlib.Path | None,
            "errors":        {step_name: "ExcType: msg"},   # empty on full success
        }
    """
    today = datetime.date.today()
    tc_expected = DOWNLOADS_DIR / f"Timecard-{today.isoformat()}.xlsx"
    er_expected = DOWNLOADS_DIR / f"Earnings-and-Hours-V1-{today.isoformat()}.xlsx"

    tc_fresh = is_fresh_download(tc_expected, min_bytes=10_000)
    er_fresh = is_fresh_download(er_expected, min_bytes=5_000) if include_earnings else False

    result: dict = {
        "timecard_xlsx": tc_expected if tc_fresh else None,
        "earnings_xlsx": er_expected if (include_earnings and er_fresh) else None,
        "errors": {},
    }

    needs_timecard = not tc_fresh
    needs_earnings = include_earnings and not er_fresh

    if not needs_timecard and not needs_earnings:
        print("[adp_bundle] SKIP browser — Layer A: required XLSX files already fresh on disk.")
        if tc_fresh:
            _mark_run_step_done("adp_timecard", note="layer_a_skip (file fresh on disk)")
        if include_earnings and er_fresh:
            _mark_run_step_done("adp_earnings", note="layer_a_skip (file fresh on disk)")
        return result

    print(f"[adp_bundle] needs_timecard={needs_timecard} needs_earnings={needs_earnings}; "
          f"opening single browser session (one login, one OTP cost).")

    with launch_persistent(
        portal="adp",
        headed=headed,
        slow_mo_ms=slow_mo_ms,
        keep_open_on_error=keep_open_on_error,
    ) as (ctx, page):
        _ensure_logged_in(page, store=store)

        if needs_timecard:
            try:
                path = _timecard_within_session(page, target_date=target_date)
                result["timecard_xlsx"] = path
                _mark_run_step_done(
                    "adp_timecard",
                    note=f"target_date={target_date.isoformat() if target_date else 'none'}",
                )
                print(f"[adp_bundle] timecard OK → {path}")
            except Exception as exc:  # noqa: BLE001
                result["errors"]["adp_timecard"] = f"{type(exc).__name__}: {exc}"
                print(f"[adp_bundle] timecard FAILED (continuing to earnings): "
                      f"{type(exc).__name__}: {exc}")
                # Per-component evidence — the launch_persistent global capture
                # only fires if the whole context with-block raises; here we
                # swallow to allow earnings to attempt.
                try:
                    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                    page.screenshot(
                        path=str(pathlib.Path.home() / ".bhaga" / "state" / "screenshots"
                                 / f"adp-bundle-timecard-fail-{ts}.png"),
                        full_page=True,
                    )
                except Exception:  # noqa: BLE001
                    pass

        if needs_earnings:
            try:
                # Reset to the v2 dashboard so the earnings flow starts from a
                # known state. Timecard left the page deep inside the iframe
                # report (or in a partial-failure state); the Reports-btn lives
                # on the dashboard chrome.
                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
                try:
                    page.wait_for_url(POST_LOGIN_URL_RE, timeout=20_000)
                except Exception:  # noqa: BLE001
                    # Session may have lapsed between scrapes (rare but ADP
                    # has been observed to expire mid-flow); re-login.
                    print("[adp_bundle] earnings: session lapsed after timecard; re-running login")
                    _ensure_logged_in(page, store=store)

                window_end = target_date or today
                window_start = window_end - datetime.timedelta(days=earnings_window_days)
                path = _earnings_within_session(
                    page, store=store, start=window_start, end=window_end
                )
                result["earnings_xlsx"] = path
                _mark_run_step_done(
                    "adp_earnings",
                    note=f"window={window_start.isoformat()}..{window_end.isoformat()}",
                )
                print(f"[adp_bundle] earnings OK → {path}")
            except Exception as exc:  # noqa: BLE001
                result["errors"]["adp_earnings"] = f"{type(exc).__name__}: {exc}"
                print(f"[adp_bundle] earnings FAILED: {type(exc).__name__}: {exc}")
                try:
                    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                    page.screenshot(
                        path=str(pathlib.Path.home() / ".bhaga" / "state" / "screenshots"
                                 / f"adp-bundle-earnings-fail-{ts}.png"),
                        full_page=True,
                    )
                except Exception:  # noqa: BLE001
                    pass

        return result


# ── CLI ────────────────────────────────────────────────────────────


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("scrape", choices=["timecard", "earnings"],
                     help="Which scrape to run.")
    cli.add_argument("--store", default="palmetto")
    cli.add_argument("--headless", action="store_true",
                     help="Run without a visible browser window.")
    cli.add_argument("--keep-open", action="store_true",
                     help="On error, leave browser open for manual inspection.")
    cli.add_argument("--start", default=None,
                     help="(earnings only) Start date YYYY-MM-DD. Default: 90 days ago.")
    cli.add_argument("--end", default=None,
                     help="(earnings only) End date YYYY-MM-DD. Default: today.")
    args = cli.parse_args()

    if args.scrape == "timecard":
        path = download_timecard(
            store=args.store,
            headed=not args.headless,
            keep_open_on_error=args.keep_open,
        )
    else:
        path = download_earnings(
            store=args.store,
            start_date=datetime.date.fromisoformat(args.start) if args.start else None,
            end_date=datetime.date.fromisoformat(args.end) if args.end else None,
            headed=not args.headless,
            keep_open_on_error=args.keep_open,
        )
    print(f"# Downloaded: {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
