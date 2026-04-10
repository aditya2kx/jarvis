#!/usr/bin/env python3
"""Robinhood navigation module — structured config for tax document retrieval.

Verified end-to-end 2026-04-05: keychain login, Robinhood app push MFA, tax documents
page, 1099 Consolidated PDF via direct link. Operator Gmail uses +hood plus-address
(see verified_login_email in PORTAL_CONFIG).
"""

PORTAL_CONFIG = {
    "name": "Robinhood",
    "keychain_service": "jarvis-robinhood",
    "verified_login_email": "aditya.2ky+hood@gmail.com",
    "verified_login_email_note": (
        "Gmail +hood alias is required — same mailbox as base email but Robinhood "
        "account is tied to this exact address."
    ),
    "login_required": True,

    "urls": {
        "login": "https://robinhood.com/login",
        "tax_docs": "https://robinhood.com/account/tax-documents",
        "account": "https://robinhood.com/account",
    },

    "login": {
        "method": "form",
        "flow": [
            "Open https://robinhood.com/login",
            "Enter email (verified_login_email — +hood matters for the correct account)",
            "Enter password from macOS Keychain item for service jarvis-robinhood",
            "Submit — wait for React SPA; MFA follows via app push (no typed code)",
        ],
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
        "success_indicator": "url contains /account or shows portfolio view after MFA approval",
    },

    "mfa": {
        "likelihood": "always",
        "methods": ["app_push", "sms", "authenticator_app"],
        "preferred": "app_push",
        "device_trust": True,
        "trigger_hint": "After password submit — Robinhood prompts for second factor",
        "flow": [
            "Robinhood sends a push notification to the Robinhood app on the user's phone",
            "User opens the app and approves the login — browser advances automatically",
            "No 6-digit code to paste when using app push (unlike SMS or TOTP)",
        ],
        "notes": (
            "Verified 2026-04-05: MFA is mobile app push approval, not SMS/TOTP for this account. "
            "Automation must pause until the user approves on device. "
            "Robinhood may still offer SMS or authenticator for other users or fallback."
        ),
    },

    "documents": [
        {
            "type": "1099 Consolidated (Securities and Crypto)",
            "name_pattern": "1099 Consolidated (Securities and Crypto) - {year}",
            "location_hint": "Tax Documents — listed by year (direct URL: urls.tax_docs)",
            "per_account": False,
            "download_format": "PDF",
            "download_method": "direct_pdf_url",
            "download_hint": "Row for the tax year exposes a direct PDF link (not only an in-app viewer)",
            "availability": "mid-February (may have corrections through March)",
            "verified_year": 2025,
        },
        {
            "type": "1099-DA",
            "name_pattern": "Form 1099-DA - Digital Assets - {year}",
            "location_hint": "Tax Documents page (crypto — separate from consolidated when applicable)",
            "per_account": False,
            "download_format": "PDF",
            "download_hint": "Download button or direct link",
            "availability": "February",
            "conditional": "Only if user had reportable digital asset activity",
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
        "After login, go to Tax Documents (menu or urls.tax_docs)",
        "Verified 2026-04-05: 1099 Consolidated (Securities and Crypto) for 2025 downloads via direct PDF URL",
        "Robinhood Cash (savings) interest may appear on a separate 1099-INT",
    ],

    "logout": {
        "url": "https://robinhood.com/account/settings",
        "confirm_text": "",
    },

    "verified": "2026-04-05",
    "verified_actions": [
        "login",
        "mfa_app_push",
        "navigate_tax_docs",
        "download_1099",
    ],
}
