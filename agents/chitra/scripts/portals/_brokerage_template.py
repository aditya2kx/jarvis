#!/usr/bin/env python3
"""Brokerage portal automation template — CHITRA Playwright MCP instructions.

Copy this file and rename for each brokerage portal (e.g. my_broker.py).
These files are gitignored — they stay local since they reveal your accounts.

CHITRA reads this file and executes the steps using CallMcpTool with
server="user-playwright".

Usage by CHITRA:
    1. Read the portal-specific copy of this file
    2. Execute each step using Playwright MCP tools
    3. Use PortalSession for credentials, OTP, and Drive upload
"""

# Fill these in for each portal:
PORTAL = "{Broker Name}"
LOGIN_URL = "https://broker.example.com/login"
TAX_FORMS_URL = "https://broker.example.com/statements/"
KEYCHAIN_SERVICE = "jarvis-{broker}"

STEPS = """
## Step 1: Initialize session
```python
from skills.browser.portal_session import PortalSession
session = PortalSession("{Broker Name}")
creds = session.get_credentials()
```

## Step 2: Navigate to login
- browser_navigate: url=LOGIN_URL
- Wait for login form to load
- Some brokers use iframes for login — check if form elements have special ref prefixes

## Step 3: Fill credentials
- browser_type: Login ID / Username field, text=creds["username"]
- browser_type: Password field, text=creds["password"]
- browser_click: Log in / Sign in button
- Wait 10-15s for login to complete (may redirect through SSO)

## Step 4: Check for MFA
- If MFA prompt appears: otp = session.request_otp(phone_hint="+1-XXX-XXX-XXXX")
- Check if email-based OTP is available (preferred for future Gmail skill autonomy)
- Some brokers skip MFA for trusted devices

## Step 5: Navigate to tax documents
- browser_navigate: url=TAX_FORMS_URL (direct URL is more reliable than clicking nav links)
- Wait for page to load (SPAs may take 3-5s)
- Look for headings like "1099 Dashboard", "Tax Forms", "Statements & Tax Forms"

## Step 6: Download 1099 from each account
- If multiple accounts: use account selector to switch between them
- For each account:
  - Find "1099 Composite" or equivalent tax form
  - Check status is "Available" / "Ready"
  - Click Download PDF button
  - Stage: session.stage_download(path, doc_type="1099", account_hint=suffix)

## Step 7: Upload and clean up
```python
session.upload_all()
```
- browser_navigate to logout URL
- session.notify_status("Downloaded {Broker} 1099s for all accounts")

## Tips
- Direct URL navigation is more reliable than clicking through SPA menus
- Account selectors may be dropdowns, tabs, or sidebar links
- Download buttons may open PDF in new tab (use browser_pdf_save) or trigger file download
- Some brokers offer XML and CSV in addition to PDF — prefer PDF for Drive upload
"""
