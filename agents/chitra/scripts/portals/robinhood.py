#!/usr/bin/env python3
"""Robinhood navigation module — structured config for tax document retrieval.

Generic navigation logic for robinhood.com. Contains zero user-specific data.
"""

PORTAL_CONFIG = {
    "name": "Robinhood",
    "keychain_service": "jarvis-robinhood",
    "login_required": True,

    "urls": {
        "login": "https://robinhood.com/login",
        "tax_docs": "https://robinhood.com/account/tax-documents",
        "account": "https://robinhood.com/account",
    },

    "login": {
        "method": "form",
        "quirks": [
            "Login is a React SPA — wait for elements to be interactive",
            "May show a CAPTCHA (hCaptcha) on suspicious logins",
        ],
        "fields": {
            "username": {"hint": "Email address input", "context": "main page"},
            "password": {"hint": "Password input", "context": "main page"},
        },
        "submit": {"hint": "Log In button", "context": "main page"},
        "post_submit_wait": 10,
        "success_indicator": "url contains /account or shows portfolio view",
    },

    "mfa": {
        "likelihood": "always",
        "methods": ["app", "sms"],
        "preferred": "app",
        "device_trust": True,
        "trigger_hint": "Two-factor authentication prompt after password entry",
        "flow": [
            "After login, shows 'Enter your authenticator app code' or 'Enter the code sent to your phone'",
            "If using authenticator app: user reads code from their auth app",
            "If using SMS: 6-digit code sent to registered phone",
            "Enter code and submit",
            "'Keep me logged in' checkbox may appear",
        ],
        "notes": (
            "Robinhood strongly encourages authenticator app over SMS. "
            "If user has app-based 2FA, there's no way to get the code via Slack — "
            "user must provide it manually. Consider asking user to switch to SMS for automation."
        ),
    },

    "documents": [
        {
            "type": "Consolidated 1099",
            "name_pattern": "Consolidated 1099 - {year}",
            "location_hint": "Tax Documents page, listed by year",
            "per_account": False,
            "download_format": "PDF",
            "download_hint": "Download button next to the 1099 entry",
            "availability": "mid-February (may have corrections through March)",
        },
        {
            "type": "1099-DA",
            "name_pattern": "Form 1099-DA - Digital Assets - {year}",
            "location_hint": "Tax Documents page (new for 2025 — crypto transactions)",
            "per_account": False,
            "download_format": "PDF",
            "download_hint": "Download button",
            "availability": "February",
            "conditional": "Only if user traded cryptocurrency",
        },
        {
            "type": "Year-End Summary",
            "name_pattern": "Year-End Summary - {year}",
            "location_hint": "Tax Documents page",
            "per_account": False,
            "download_format": "PDF",
            "download_hint": "Download link",
            "availability": "January",
        },
    ],

    "account_selector": {
        "exists": False,
        "note": "Single unified account (stocks, crypto, cash in one account)",
    },

    "quirks": [
        "React SPA — page loads are not traditional navigations, wait for content rendering",
        "hCaptcha may block automated logins — may need to use existing browser session",
        "1099 corrections are common — Robinhood often issues corrected 1099s in March",
        "Tax documents page directly accessible via URL (don't navigate through menus)",
        "Crypto transactions now get separate Form 1099-DA (starting 2025)",
        "Robinhood Cash (savings) interest may appear on a separate 1099-INT",
    ],

    "logout": {
        "url": "https://robinhood.com/account/settings",
        "confirm_text": "",
    },
}
