#!/usr/bin/env python3
"""Wells Fargo navigation module — structured config for tax document retrieval.

Generic URLs and field hints apply to any Wells Fargo Online user. Verified run notes
(document picking, MFA path) reflect a successful 2026-04-05 session and may include
property/account disambiguation for multi-mortgage households.
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
            "Login uses SAML and cross-origin redirects during sign-on — JS credential "
            "interceptors (e.g. wrapping fetch/XHR for autofill) often lose state across "
            "those navigations; prefer native Playwright fill/submit on the live page DOM.",
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

    "post_login_navigation": {
        "target": "Tax Documents / Statements",
        "steps": [
            "After sign-on completes, go to Tax Documents for IRS forms (1098, 1099-INT, etc.)",
            "Deep link: urls.tax_docs — or use Statements (urls.statements) for year-end mortgage statements",
        ],
    },

    "mfa": {
        "likelihood": "conditional",
        "methods": ["email", "sms", "phone_call"],
        "preferred": "email",
        "verified_method": "email",
        "device_trust": True,
        "trigger_hint": "Identity verification page asking how to receive a code",
        "flow": [
            "Shows 'We need to verify your identity' with delivery options",
            "Verified path: choose email — Wells Fargo sends a verification code to the registered email address",
            "Enter the code from email",
            "Optional: 'Save this computer' — select Yes when appropriate",
        ],
        "notes": "Email MFA verified 2026-04-05. Other channels (SMS, call) may still appear depending on profile.",
    },

    "documents": [
        {
            "type": "Form 1098",
            "name_pattern": "Form 1098 Mortgage Interest Statement - {year}",
            "location_hint": "Tax Documents section, listed under Mortgage",
            "per_account": True,
            "download_format": "PDF",
            "download_method": "direct_pdf_link",
            "download_hint": "Open the correct mortgage row, then use the direct PDF download link (not a fragile embedded viewer-only path)",
            "availability": "January 31",
            "verified_selection": {
                "note": "Households with multiple mortgages: pick the 1098 for the rental property, not the primary residence.",
                "example": {
                    "property": "Brisbane rental — 211 Golden Eagle Ln",
                    "account_suffix": "5503",
                },
            },
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
        "SAML-heavy login: avoid credential injection patterns that assume a single origin through the whole flow",
        "Tax documents page organizes forms by account type (Banking, Mortgage, etc.)",
        "Mortgage 1098 is the primary tax document for most users; multiple mortgages appear as separate rows — match account/address to the intended property",
        "Transaction export is useful for rental expense documentation",
        "Wells Fargo may show interstitial security pages ('We noticed unusual activity')",
        "Session timeout is relatively short (~10 minutes)",
    ],

    "logout": {
        "url": "https://connect.secure.wellsfargo.com/auth/logout",
        "confirm_text": "You have been signed off",
    },

    "verified": "2026-04-05",
    "verified_actions": ["login", "mfa_email", "navigate_tax_docs", "download_1098"],
}
