#!/usr/bin/env python3
"""Fidelity navigation module — structured config for tax document retrieval.

Generic navigation logic for fidelity.com. Contains zero user-specific data.
Covers: brokerage accounts, 401(k), IRA, Roth, HSA (if Fidelity-custodied).
"""

PORTAL_CONFIG = {
    "name": "Fidelity",
    "keychain_service": "jarvis-fidelity",
    "login_required": True,

    "urls": {
        "login": "https://digital.fidelity.com/prgw/digital/login/full-page",
        "tax_docs": "https://digital.fidelity.com/ftgw/digital/tax-documents/",
        "statements": "https://digital.fidelity.com/ftgw/digital/documents/",
        "summary": "https://digital.fidelity.com/ftgw/digital/portfolio/summary",
        "logout": "https://login.fidelity.com/ftgw/Fidelity/RtlCust/Logout/Init",
    },

    "login": {
        "method": "form",
        "quirks": [
            "Login page is not in an iframe — standard form elements",
            "May redirect through multiple SSO pages",
            "Fidelity frequently updates their login page design",
        ],
        "fields": {
            "username": {"hint": "Username input field", "context": "main page"},
            "password": {"hint": "Password input field", "context": "main page"},
        },
        "submit": {"hint": "Log In button", "context": "main page"},
        "post_submit_wait": 10,
        "success_indicator": "url contains 'portfolio/summary' or 'digital/portfolio'",
    },

    "mfa": {
        "likelihood": "conditional",
        "methods": ["sms", "phone_call", "app"],
        "preferred": "sms",
        "device_trust": True,
        "trigger_hint": "Security code verification page with phone number options",
        "flow": [
            "Shows 'Verify your identity' page with registered devices",
            "Select phone number or authenticator app",
            "Click 'Send code' or 'Call me'",
            "Enter received code",
            "'Don't ask again on this device' checkbox — check it",
        ],
        "notes": "MFA frequency depends on device trust. Saved devices may skip entirely.",
    },

    "documents": [
        {
            "type": "1099",
            "name_pattern": "Consolidated 1099 - {year}",
            "location_hint": "Tax Documents page, 'Tax Forms' tab, sorted by year",
            "per_account": True,
            "download_format": "PDF",
            "download_hint": "Download or View PDF link next to each form",
            "availability": "mid-February (brokerage), March (retirement)",
        },
        {
            "type": "Form 1099-R",
            "name_pattern": "Form 1099-R Distribution - {year}",
            "location_hint": "Tax Documents page — appears if distributions were taken from 401k/IRA",
            "per_account": True,
            "download_format": "PDF",
            "download_hint": "Download link",
            "availability": "January 31",
            "conditional": "Only if retirement account distributions occurred",
        },
        {
            "type": "Form 5498",
            "name_pattern": "Form 5498 IRA Contribution - {year}",
            "location_hint": "Tax Documents page — IRA/HSA contribution information",
            "per_account": True,
            "download_format": "PDF",
            "download_hint": "Download link",
            "availability": "May (final due date May 31)",
            "conditional": "Sent for IRA/HSA contribution reporting",
        },
        {
            "type": "Annual Account Statement",
            "name_pattern": "Year-End Statement - {year}",
            "location_hint": "Documents page → Statements tab",
            "per_account": True,
            "download_format": "PDF",
            "download_hint": "Download link for December/Annual statement",
            "availability": "January",
        },
    ],

    "account_selector": {
        "exists": True,
        "hint": "Account selector dropdown at top of Tax Documents page",
        "note": "Shows all account types: Individual, Joint, IRA, 401(k), HSA",
        "wait_after_switch": 3,
        "account_types": ["Individual Brokerage", "Joint Brokerage", "Traditional IRA",
                          "Roth IRA", "401(k)", "HSA"],
    },

    "quirks": [
        "Employer-sponsored 401(k) may be on a separate NetBenefits portal (nb.fidelity.com)",
        "Tax forms may take a few seconds to load after selecting an account",
        "Fidelity issues corrected 1099s — check for 'Corrected' badge",
        "Form 5498 arrives in May, well after tax filing deadline",
        "HSA custodied by Fidelity appears in the same account list",
        "Some employer plans use Fidelity's NetBenefits — different URL and login",
        "Year-end statements are not tax forms but useful for reconciliation",
    ],

    "logout": {
        "url": "https://login.fidelity.com/ftgw/Fidelity/RtlCust/Logout/Init",
        "confirm_text": "You have been logged out",
    },
}
