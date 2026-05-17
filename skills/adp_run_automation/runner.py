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
        if any(t in url for t in ("verify", "challenge", "step-up", "mfa")):
            _raise_with_evidence(
                page, store=store,
                reason="ADP demanded a verification / 2FA step. OTP flow is not wired. "
                       "Either complete 2FA manually in your own Chrome to renew the trust window, "
                       "or migrate ADP to a TOTP method we can read programmatically.",
            )
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
    headed: bool = True,
    slow_mo_ms: int = 50,
    keep_open_on_error: bool = False,
) -> pathlib.Path:
    """Open Reports > Time reports > Timecard, select all available pay periods,
    apply changes, click Export to Excel, save .xlsx."""
    with launch_persistent(
        portal="adp",
        headed=headed,
        slow_mo_ms=slow_mo_ms,
        keep_open_on_error=keep_open_on_error,
    ) as (ctx, page):
        _ensure_logged_in(page, store=store)

        # Navigate to Timecard: Reports-btn → Reports landing → "View all
        # reports" opens the Single Reports modal → expand "Time reports"
        # accordion → click "Timecard". ADP dashboard maintains persistent
        # connections so 'networkidle' never fires; use targeted waits.
        page.locator('[data-test-id="Reports-btn"]').first.click()
        # After Reports-btn click ADP can land on either:
        #   (a) the Reports landing page (test-id="view-all-reports") OR
        #   (b) the homepage Reports widget (test-id="reports-tile-view-all-reports-button")
        # Try both via a CSS-OR selector.
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
        # Iframe loads its content async, give it a beat.
        page.wait_for_timeout(4_000)
        frame = page.frame_locator("iframe[name='mdfTimeFrame']")

        # Select All pay periods. There are TWO comboboxes with "Pay Period" in
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
        # The multi-select listbox has a "Select All" checkbox at the top.
        # Falls back to clicking every "Pay Period: …" option if the listbox
        # implementation doesn't expose a Select All.
        try:
            select_all = frame.get_by_role(
                "option", name=re.compile(r"^Select All$", re.I)
            ).first
            select_all.wait_for(state="visible", timeout=5_000)
            select_all.click()
        except Exception:
            # Fallback: click each pay-period option individually.
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
            # Fallback: keyboard nav
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Enter")
        page.wait_for_timeout(400)

        # Apply Changes
        frame.get_by_role("button", name=re.compile(r"^Apply Changes$", re.I)).first.click()
        # Report body re-renders; takes a few seconds.
        page.wait_for_timeout(8_000)

        # Export To Excel — the SDF-BUTTON custom element. Try selector first, then JS.
        excel_btn_selector = "#report-excel-button"
        try:
            frame.locator(excel_btn_selector).first.wait_for(state="visible", timeout=10_000)
        except Exception:
            # Wait longer if report is still rendering.
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
    AND capture the most recent pay-period's Credit Card Tips Owed)."""
    profile = _load_store_profile(store)
    report_name = profile["adp_run"].get("wage_rate_report_name", "Earnings and Hours V1")
    today = datetime.date.today()
    start = start_date or (today - datetime.timedelta(days=90))
    end = end_date or today

    with launch_persistent(
        portal="adp",
        headed=headed,
        slow_mo_ms=slow_mo_ms,
        keep_open_on_error=keep_open_on_error,
    ) as (ctx, page):
        _ensure_logged_in(page, store=store)

        page.locator('[data-test-id="Reports-btn"]').first.click()
        page.wait_for_load_state("networkidle", timeout=15_000)
        page.wait_for_timeout(1_500)

        # Scroll to "My saved custom reports" section and click the report.
        # The link's accessible name matches the saved-report title.
        report_link = page.get_by_role("link", name=re.compile(rf"^{re.escape(report_name)}$", re.I)).first
        report_link.scroll_into_view_if_needed()
        report_link.click()
        page.wait_for_load_state("networkidle", timeout=15_000)
        page.wait_for_timeout(2_000)

        # Custom report builder modal opens (NO iframe).
        # 1. Set Date range = "Custom date range"
        date_range_combo = page.get_by_role("combobox", name=re.compile(r"Date range", re.I)).first
        date_range_combo.click()
        page.wait_for_timeout(300)
        page.get_by_role("option", name=re.compile(r"Custom date range", re.I)).first.click()
        page.wait_for_timeout(500)

        # 2. From / To textboxes
        page.get_by_role("textbox", name=re.compile(r"From", re.I)).first.fill(start.strftime("%m/%d/%Y"))
        page.get_by_role("textbox", name=re.compile(r"To", re.I)).first.fill(end.strftime("%m/%d/%Y"))
        page.keyboard.press("Tab")
        page.wait_for_timeout(500)

        # 3. Click "Preview report"
        page.locator("[data-test-id='view-custom-report']").first.click()
        # Wait for preview grid to render
        page.wait_for_timeout(4_000)

        # 4. Open Download dropdown
        page.get_by_role("button", name=re.compile(r"^Download$", re.I)).first.click()
        page.wait_for_timeout(500)
        page.get_by_role("menuitem", name=re.compile(r"Excel \(\.xlsx\)", re.I)).first.click()

        # 5. Wait for "Your report is ready to download" dialog (~3-10s)
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
