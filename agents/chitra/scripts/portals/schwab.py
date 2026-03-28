#!/usr/bin/env python3
"""Schwab navigation module — structured config for tax document retrieval.

Generic navigation logic for schwab.com. Contains zero user-specific data.
Any Schwab account holder can use this module as-is.
"""

PORTAL_CONFIG = {
    "name": "Charles Schwab",
    "keychain_service": "jarvis-schwab",
    "login_required": True,

    "urls": {
        "login": "https://www.schwab.com/public/schwab/nn/login/login.html",
        "tax_docs": "https://client.schwab.com/app/accounts/statements/",
        "summary": "https://client.schwab.com/app/accounts/summary",
        "logout": "https://client.schwab.com/logout/logout.aspx?explicit=y",
    },

    "login": {
        "method": "form",
        "quirks": [
            "Login form is rendered inside an IFRAME — element refs get 'f4e' prefix",
            "Redirects from schwab.com to client.schwab.com/Areas/Access/Login",
        ],
        "fields": {
            "username": {"hint": "Login ID textbox", "context": "inside iframe"},
            "password": {"hint": "Password textbox", "context": "inside iframe"},
        },
        "submit": {"hint": "Log in button", "context": "inside iframe"},
        "post_submit_wait": 15,
        "success_indicator": "url contains 'accounts/summary'",
    },

    "mfa": {
        "likelihood": "conditional",
        "methods": ["sms", "email"],
        "preferred": "email",
        "device_trust": True,
        "trigger_hint": "Verification code prompt page",
        "notes": "Some logins skip MFA entirely (device trust / remembered browser)",
    },

    "documents": [
        {
            "type": "1099",
            "name_pattern": "1099 Composite and Year-End Summary - {year}",
            "location_hint": "1099 Dashboard heading on the Statements page",
            "per_account": True,
            "download_format": "PDF",
            "download_hint": "Click to Download PDF button",
            "availability": "mid-February",
            "columns": "Account | Status | Document | Download",
            "status_field": "AVAILABLE when ready",
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
        "hint": "Account Selector button at top of Statements page",
        "note": "Click the button TEXT, not the surrounding container",
        "wait_after_switch": 5,
        "account_types": ["Individual Brokerage", "Stock Plan", "Equity Awards"],
    },

    "quirks": [
        "Login form is inside an IFRAME — Playwright element refs have different prefix than outer page",
        "Statements page is a SPA — needs 5s+ to load after navigation",
        "Direct URL navigation is more reliable than clicking SPA nav links",
        "Account selector: click button text, not container, for reliable activation",
        "Equity Award Center may have 'Stock Plan Supplement' separate from the main 1099",
        "Session timeout ~15 minutes of inactivity; re-login if needed",
        "Download may open in new tab or trigger direct download — handle both cases",
    ],

    "logout": {
        "url": "https://client.schwab.com/logout/logout.aspx?explicit=y",
        "confirm_text": "You are now logged off",
    },
}
