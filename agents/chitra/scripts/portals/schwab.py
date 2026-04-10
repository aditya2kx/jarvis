#!/usr/bin/env python3
"""Schwab navigation module — structured config for tax document retrieval.

Generic navigation logic for schwab.com. Keychain stores credentials only;
session-specific verified notes may reference example account suffixes from validation runs.
"""

PORTAL_CONFIG = {
    "name": "Charles Schwab",
    "keychain_service": "jarvis-schwab",
    "login_required": True,

    "urls": {
        "login": "https://client.schwab.com/Login/SignOn/CustomerCenterLogin.aspx",
        "tax_docs": "https://client.schwab.com/app/accounts/statements/",
        "summary": "https://client.schwab.com/app/accounts/summary",
        "logout": "https://client.schwab.com/logout/logout.aspx?explicit=y",
    },

    "login": {
        "method": "iframe",
        "description": "Schwab renders the login form inside an iframe on the Customer Center login page.",
        "quirks": [
            "Target the login iframe — username/password and Log in live inside it (Playwright refs often differ from outer page).",
            "After success, land on client area (e.g. accounts/summary); then open Statements & Tax Forms for tax PDFs.",
        ],
        "fields": {
            "username": {"hint": "Login ID textbox", "context": "inside login iframe"},
            "password": {"hint": "Password textbox", "context": "inside login iframe"},
        },
        "submit": {"hint": "Log in button", "context": "inside login iframe"},
        "post_submit_wait": 15,
        "success_indicator": "url contains 'accounts/summary' or other authenticated client.schwab.com app path",
        "post_login_navigation": [
            "From authenticated home/summary, go to Statements & Tax Forms (Statements area) for 1099 and year-end PDFs.",
            "Direct URL to statements hub is reliable: /app/accounts/statements/",
        ],
    },

    "mfa": {
        "likelihood": "conditional",
        "methods": ["sms", "email"],
        "preferred": "email",
        "device_trust": True,
        "trigger_hint": "Verification code prompt page (if Schwab requests it)",
        "notes": "No MFA step observed during verified session (2026-04-05); login completed after iframe credentials only. Other accounts or browsers may still see SMS/email verification.",
    },

    "documents": [
        {
            "type": "1099",
            "name_pattern": "1099 Composite and Year-End Summary - {year}",
            "location_hint": "Statements & Tax Forms / Statements area — 1099 Dashboard-style listing",
            "per_account": True,
            "download_format": "PDF",
            "download_method": "direct_pdf_url",
            "download_hint": "Documents expose direct PDF links — fetch or open URL; not only a synthetic 'Download' button",
            "availability": "mid-February",
            "columns": "Account | Status | Document | Download",
            "status_field": "AVAILABLE when ready",
            "verified": "1099 Composite and Year-End Summary available per account when listed (verified accounts ending …965 and …3771, 2026-04-05).",
        },
        {
            "type": "Stock Plan Supplement",
            "name_pattern": "Stock Plan Transaction Supplement - {year}",
            "location_hint": "Equity Award Center section (separate from main 1099)",
            "per_account": True,
            "download_format": "PDF",
            "download_hint": "Download link in Equity Awards section",
            "availability": "mid-February",
        },
    ],

    "account_selector": {
        "exists": True,
        "hint": "Account selector on Statements / tax area allows switching between accounts",
        "note": "Click the button TEXT, not the surrounding container, for reliable activation",
        "wait_after_switch": 5,
        "account_types": ["Individual Brokerage", "Stock Plan", "Equity Awards"],
        "verified": "Switching accounts in UI confirmed; repeat downloads per account as needed (suffixes …965, …3771 on 2026-04-05).",
    },

    "quirks": [
        "Login form is inside an IFRAME on CustomerCenterLogin.aspx — scope locators to the login iframe.",
        "Statements area is SPA-like — allow 5s+ after navigation for content to settle.",
        "Direct URL to /app/accounts/statements/ is reliable after login.",
        "Account selector: click button text, not container, for reliable activation.",
        "Equity Award Center may list 'Stock Plan Supplement' separately from the main 1099 Composite.",
        "Session timeout ~15 minutes of inactivity; re-login if needed.",
        "Tax PDFs: direct PDF URLs — handle new tab or same-tab navigation as well as direct download.",
    ],

    "logout": {
        "url": "https://client.schwab.com/logout/logout.aspx?explicit=y",
        "confirm_text": "You are now logged off",
        "notes": "Explicit logout URL works; clean session end observed in verification.",
    },

    "verified": "2026-04-05",
    "verified_actions": [
        "login",
        "navigate_tax_forms",
        "download_1099_composite",
        "account_switch",
        "logout",
    ],
}
