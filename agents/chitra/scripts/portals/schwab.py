#!/usr/bin/env python3
"""Schwab portal automation — instructions for CHITRA driving Playwright MCP.

This is NOT a standalone script. It documents the exact Playwright MCP calls
CHITRA should make to download tax documents from Schwab.

CHITRA reads this file and executes the steps using CallMcpTool with
server="user-playwright".

Usage by CHITRA:
    1. Read this file to understand the navigation steps
    2. Execute each step using Playwright MCP tools
    3. Use PortalSession for credentials, OTP, and Drive upload
"""

PORTAL = "Schwab"
LOGIN_URL = "https://www.schwab.com/public/schwab/nn/login/login.html"
TAX_FORMS_URL = "https://client.schwab.com/app/accounts/statements/"
KEYCHAIN_SERVICE = "jarvis-schwab"

STEPS = """
## Step 1: Initialize session
```python
from skills.browser.portal_session import PortalSession
session = PortalSession("Schwab")
creds = session.get_credentials()
```

## Step 2: Navigate to login
- browser_navigate: url="https://www.schwab.com/public/schwab/nn/login/login.html"
- Wait for redirect to client.schwab.com/Areas/Access/Login
- Wait ~5s for iframe to load (login form is inside iframe)

## Step 3: Fill credentials
- The login form is INSIDE AN IFRAME. Playwright refs will have "f4e" prefix.
- browser_type: ref for Login ID textbox, text=creds["username"]
- browser_type: ref for Password textbox, text=creds["password"]
- browser_click: ref for Log in button
- Wait 15s for login to complete

## Step 4: Check for MFA
- If page stays on Login URL with MFA prompt → session.request_otp()
- MFA may or may not be required depending on device trust
- If MFA appears, it's typically SMS to registered phone

## Step 5: Verify dashboard loaded
- URL should be: client.schwab.com/app/accounts/summary
- Look for "Statements & Tax Forms" in secondary nav

## Step 6: Navigate to Statements & Tax Forms
- browser_navigate: url="https://client.schwab.com/app/accounts/statements/"
- Wait 5s for the SPA to load
- Look for "1099 Dashboard" heading

## Step 7: Download 1099 from each account

### For the default account (shown first):
- Look for the 1099 Dashboard table
- Find row with "1099 Composite and Year-End Summary - {YYYY}"
- Status should say "AVAILABLE"
- Click "Click to Download PDF" button

### Switch to next account:
- Click the Account Selector button
- Look for other accounts in the dropdown
- Click the account you haven't downloaded yet
- Wait 5s for statements to reload
- Download the 1099 for that account too

## Step 8: Stage and upload downloads
```python
for f in downloaded_files:
    session.stage_download(f, doc_type="1099", issuer="Charles Schwab", account_hint=acct_suffix)
session.upload_all()
```

## Step 9: Log out and notify
- browser_navigate: url="https://client.schwab.com/logout/logout.aspx?explicit=y"
- session.notify_status("Downloaded Schwab 1099s for all accounts")

## Known Issues / Tips
- Login form is in an iframe — refs have "f4e" prefix
- The Statements page loads via SPA — wait for "1099 Dashboard" to appear
- Account selector shows all account types (brokerage, stock plan, equity awards)
- Equity Award Center may have a separate "Stock Plan Supplement" — check if available
- Download buttons: PDF | XML | CSV — prefer PDF for Drive upload
- The SPA navigation can timeout — use browser_navigate with direct URLs instead of clicking links
"""
