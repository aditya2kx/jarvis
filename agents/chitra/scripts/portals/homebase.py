#!/usr/bin/env python3
"""Homebase (payroll) navigation module — structured config for tax documents.

Generic navigation logic for joinhomebase.com. Contains zero user-specific data.
Homebase provides payroll, scheduling, and HR for small businesses.
"""

PORTAL_CONFIG = {
    "name": "Homebase",
    "keychain_service": "jarvis-homebase",
    "login_required": True,

    "urls": {
        "login": "https://app.joinhomebase.com/accounts/sign-in",
        "tax_docs": "https://app.joinhomebase.com/payroll/tax_documents",
        "payroll": "https://app.joinhomebase.com/payroll",
    },

    "login": {
        "method": "form",
        "quirks": [
            "Standard email/password form",
            "May redirect to business selection if user has multiple locations",
        ],
        "fields": {
            "username": {"hint": "Email address input", "context": "main page"},
            "password": {"hint": "Password input", "context": "main page"},
        },
        "submit": {"hint": "Sign in button", "context": "main page"},
        "post_submit_wait": 8,
        "success_indicator": "url contains '/dashboard' or shows business name",
    },

    "mfa": {
        "likelihood": "never",
        "methods": [],
        "notes": "Homebase typically does not have MFA for small business accounts",
    },

    "documents": [
        {
            "type": "W-2",
            "name_pattern": "W-2 Employee Wage Statement - {year}",
            "location_hint": "Tax Documents section → W-2s",
            "per_account": True,
            "download_format": "PDF",
            "download_hint": "Download button next to each employee W-2",
            "availability": "January 31",
            "notes": "One W-2 per employee — download all",
        },
        {
            "type": "W-3",
            "name_pattern": "W-3 Transmittal of Wage Statements - {year}",
            "location_hint": "Tax Documents section → W-3",
            "per_account": False,
            "download_format": "PDF",
            "download_hint": "Download button",
            "availability": "January 31",
        },
        {
            "type": "Form 941",
            "name_pattern": "Form 941 Quarterly Federal Tax Return - Q{quarter} {year}",
            "location_hint": "Tax Documents section → Quarterly Forms",
            "per_account": False,
            "download_format": "PDF",
            "download_hint": "Download button for each quarter",
            "availability": "End of month following quarter (Apr 30, Jul 31, Oct 31, Jan 31)",
            "notes": "Four per year (Q1-Q4). Download all four.",
        },
        {
            "type": "Form 940",
            "name_pattern": "Form 940 Annual Federal Unemployment Tax - {year}",
            "location_hint": "Tax Documents section → Annual Forms",
            "per_account": False,
            "download_format": "PDF",
            "download_hint": "Download button",
            "availability": "January 31",
        },
    ],

    "account_selector": {
        "exists": True,
        "hint": "Business/location selector if multiple locations exist",
        "note": "Select each location to see its tax documents",
        "wait_after_switch": 3,
    },

    "quirks": [
        "Tax documents section is under Payroll → Tax Documents (not main nav)",
        "Form 941 has FOUR entries per year (one per quarter) — download all",
        "If business has multiple locations, each may have separate tax docs",
        "W-2s are per employee — make sure to download ALL employee W-2s",
        "Homebase may also generate state-specific tax forms (SUI, etc.)",
        "Year-end forms (W-2, W-3, 940) available by January 31",
        "Quarterly 941s available after each quarter ends",
    ],

    "logout": {
        "url": "https://app.joinhomebase.com/accounts/sign-out",
        "confirm_text": "",
    },
}
