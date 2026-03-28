#!/usr/bin/env python3
"""Schwab navigation module — Playwright MCP steps for tax document retrieval.

Generic navigation logic for schwab.com. Contains zero user-specific data.
Any Schwab account holder can use this module as-is.

Quirks discovered during testing:
- Login form is rendered inside an IFRAME (refs get "f4e" prefix)
- Statements page is a SPA — needs 5s+ to load after navigation
- Account selector dropdown shows all account types (brokerage, stock plan, equity awards)
- MFA may or may not trigger depending on device trust
- Direct URL navigation is more reliable than clicking SPA nav links
"""

PORTAL = "Schwab"
KEYCHAIN_SERVICE = "jarvis-schwab"

URLS = {
    "login": "https://www.schwab.com/public/schwab/nn/login/login.html",
    "statements": "https://client.schwab.com/app/accounts/statements/",
    "summary": "https://client.schwab.com/app/accounts/summary",
    "logout": "https://client.schwab.com/logout/logout.aspx?explicit=y",
}

LOGIN_STEPS = """
## Login
1. browser_navigate → URLS["login"]
   - Redirects to client.schwab.com/Areas/Access/Login
2. Wait ~5s for iframe to load
   - Login form is INSIDE AN IFRAME — element refs will have "f4e" prefix
3. browser_type → Login ID textbox (inside iframe), text=username
4. browser_type → Password textbox (inside iframe), text=password
5. browser_click → "Log in" button (inside iframe)
6. Wait 15s — login may redirect through multiple pages

## MFA (if triggered)
- Page will show verification code prompt
- Check for "verify another way" option → email is preferred (future Gmail autonomy)
- If only SMS: session.request_otp(phone_hint=masked_phone_from_page)
- Enter code, submit
- Some logins skip MFA entirely (device trust / remembered browser)

## Verify success
- URL should be: client.schwab.com/app/accounts/summary
- Look for "Statements & Tax Forms" in secondary nav bar
"""

TAX_DOCS_STEPS = """
## Navigate to tax forms
1. browser_navigate → URLS["statements"]  (direct URL, don't click nav links)
2. Wait 5s for SPA to load
3. Look for "1099 Dashboard" heading — this is the tax forms section

## 1099 Dashboard
- Shows a table with columns: Account | Status | Document | Download
- Status will say "AVAILABLE" when the 1099 is ready
- Document name: "1099 Composite and Year-End Summary - {YYYY}"
- Download options: PDF | XML | CSV — use PDF for Drive upload

## Multiple accounts
- An Account Selector button appears at the top of the page
- Click it to see a dropdown of all accounts (brokerage, stock plan, equity awards)
- Each account may have its own 1099
- Switch accounts → wait 5s → download each 1099

## Statements section (below 1099 Dashboard)
- Shows a searchable table of all statements and tax forms
- Filter by: Date range, Document Types (Statements / Tax Forms / Letters / Reports)
- Tax Forms filter shows 1099s with date, type, account, and download buttons

## Download
- Click "Click to Download PDF" button
- May open in new tab or trigger direct download (handle both cases)
- Filename pattern: varies, rename to "{YYYY} 1099 Composite - Schwab {suffix}.pdf"
"""

LOGOUT_STEPS = """
## Logout
- browser_navigate → URLS["logout"]
- Confirms "You are now logged off"
"""

KNOWN_ISSUES = """
- iframe login: Playwright refs inside the iframe have a different prefix than outer page
- SPA timeouts: The statements page occasionally takes 10s+ to load
- Account selector: Click the button text (not the surrounding container) for reliable activation
- Equity Award Center: May have a "Stock Plan Supplement" document separate from 1099
- Session timeout: ~15 minutes of inactivity; re-login if needed
"""
