#!/usr/bin/env python3
"""E*Trade / Morgan Stanley navigation module — Playwright MCP steps.

Generic navigation logic for us.etrade.com. Contains zero user-specific data.
Any E*Trade account holder can use this module as-is.

Quirks discovered during testing:
- Login always requires MFA (SMS only, no email option as of 2026-03)
- "Verify another way" only offers entering a different phone number, not email
- Login form is NOT in an iframe (unlike Schwab)
- Has a "save this device" option during MFA — select Yes to skip MFA next time
"""

PORTAL = "E*Trade"
KEYCHAIN_SERVICE = "jarvis-etrade"

URLS = {
    "login": "https://us.etrade.com/etx/hw/auth",
    "tax_docs": "https://us.etrade.com/etx/pxy/my-account/documents",
    "logout": "https://us.etrade.com/etx/pxy/logout",
}

LOGIN_STEPS = """
## Login
1. browser_navigate → URLS["login"]
   - Redirects to us.etrade.com/etx/pxy/login with TARGET param
2. Form fields are on the main page (no iframe):
   - browser_type → "User ID" textbox, text=username
   - browser_type → "Password" textbox, text=password
3. Note: "Use security code" checkbox — leave unchecked
4. browser_click → "Log on" button
5. A loading dialog appears briefly

## MFA (always required on new devices)
- Page redirects to /login/sendotpcode
- Shows: "Help us confirm your identity"
- Displays masked phone number: +1-XXX-XXX-XXXX
- "verify another way" button → only offers entering a different phone number (no email)
- browser_click → "Send Code" button
- Request OTP via Slack: session.request_otp(phone_hint=masked_phone)

## Enter verification code
- Page redirects to /login/verifyotpcode
- browser_type → "Enter verification code" textbox, text=otp_code
- "Save this device?" radio buttons → select "Yes, save this device."
- browser_click → "Submit" button

## Verify success
- Should redirect to account overview / dashboard
"""

TAX_DOCS_STEPS = """
## Navigate to tax documents
1. browser_navigate → URLS["tax_docs"] or look for "Documents" in nav
2. Look for Tax Documents section
3. Filter by year if available

## Download
- Find 1099 Composite or equivalent
- Click download/view button
- Also look for "Stock Plan Supplement" if user has equity awards
- Rename files to standard naming convention
"""

KNOWN_ISSUES = """
- MFA is mandatory on new devices — no way to skip it
- SMS is the only MFA option (no email, no authenticator app)
- "verify another way" is misleading — it just lets you enter a different phone number
- The login flow uses multiple page redirects (not SPA)
- Session cookies: if "save this device" was selected, future logins may skip MFA
- Footer mentions "Need tax documents for a closed account?" — old credentials may work
"""
