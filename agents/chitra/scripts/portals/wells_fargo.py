#!/usr/bin/env python3
"""Wells Fargo navigation module — structured config for tax document retrieval.

Generic navigation logic for wellsfargo.com. Contains zero user-specific data.
Covers: mortgage (Form 1098), bank accounts (1099-INT), transaction exports.
"""

PORTAL_CONFIG = {
    "name": "Wells Fargo",
    "keychain_service": "jarvis-wellsfargo",
    "login_required": True,

    "urls": {
        "login": "https://connect.secure.wellsfargo.com/auth/login/present",
        "tax_docs": "https://connect.secure.wellsfargo.com/accounts/documents/tax",
        "statements": "https://connect.secure.wellsfargo.com/accounts/documents/statements",
        "accounts": "https://connect.secure.wellsfargo.com/accounts/start",
        "logout": "https://connect.secure.wellsfargo.com/auth/logout",
    },

    "login": {
        "method": "form",
        "quirks": [
            "Login page has been modernized — uses React-based SPA",
            "May show a 'Sign on to Wells Fargo Online' page with username first, then password on next step",
            "Some flows show username + password on the same page",
        ],
        "fields": {
            "username": {"hint": "Username / User ID input", "context": "main page"},
            "password": {"hint": "Password input", "context": "may be on second step"},
        },
        "submit": {"hint": "Sign On button", "context": "main page"},
        "post_submit_wait": 10,
        "success_indicator": "url contains 'accounts/start' or shows account summary",
    },

    "mfa": {
        "likelihood": "conditional",
        "methods": ["sms", "phone_call", "email"],
        "preferred": "email",
        "device_trust": True,
        "trigger_hint": "Identity verification page asking how to receive a code",
        "flow": [
            "Shows 'We need to verify your identity' with delivery options",
            "Options: text message, phone call, or email",
            "Select preferred method and click 'Continue'",
            "Enter the received code",
            "'Save this computer' option — select Yes",
        ],
        "notes": "Wells Fargo supports email-based MFA — good for future Gmail autonomy.",
    },

    "documents": [
        {
            "type": "Form 1098",
            "name_pattern": "Form 1098 Mortgage Interest Statement - {year}",
            "location_hint": "Tax Documents section, listed under Mortgage",
            "per_account": True,
            "download_format": "PDF",
            "download_hint": "Download or View PDF button",
            "availability": "January 31",
        },
        {
            "type": "1099-INT",
            "name_pattern": "Form 1099-INT Interest Income - {year}",
            "location_hint": "Tax Documents section, listed under Banking",
            "per_account": True,
            "download_format": "PDF",
            "download_hint": "Download button",
            "availability": "January 31",
            "conditional": "Only if interest earned > $10",
        },
        {
            "type": "Year-End Mortgage Statement",
            "name_pattern": "Annual Mortgage Statement - {year}",
            "location_hint": "Statements section under Mortgage",
            "per_account": True,
            "download_format": "PDF",
            "download_hint": "Download link for December/Annual statement",
            "availability": "January",
        },
    ],

    "transaction_export": {
        "available": True,
        "hint": "Account Activity page → Download/Export transactions",
        "formats": ["CSV", "QFX", "QBO"],
        "useful_for": "Rental property expense tracking (mortgage payments, repairs)",
        "date_range": "Custom date range selector — set to full tax year",
    },

    "account_selector": {
        "exists": True,
        "hint": "Tax documents are grouped by account type (Banking, Mortgage, Investment)",
        "note": "No single dropdown — documents are already categorized on the tax docs page",
        "wait_after_switch": 3,
    },

    "quirks": [
        "Tax documents page organizes forms by account type (Banking, Mortgage, etc.)",
        "Mortgage 1098 is the primary tax document for most users",
        "Transaction export is useful for rental expense documentation",
        "Wells Fargo may show interstitial security pages ('We noticed unusual activity')",
        "Multiple mortgages show as separate entries under the Mortgage section",
        "Session timeout is relatively short (~10 minutes)",
        "Wells Fargo supports email MFA — prefer this for future Gmail skill autonomy",
    ],

    "logout": {
        "url": "https://connect.secure.wellsfargo.com/auth/logout",
        "confirm_text": "You have been signed off",
    },
}
