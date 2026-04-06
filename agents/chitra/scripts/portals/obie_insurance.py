#!/usr/bin/env python3
"""Obie Insurance navigation module — rental property insurance policies and declarations.

Verified working 2026-04-06. Login + policy download tested.
"""

PORTAL_CONFIG = {
    "name": "Obie Insurance",
    "keychain_service": "jarvis-obie",
    "login_required": True,

    "urls": {
        "login": "https://app.obieinsurance.com/",
        "policies": "https://app.obieinsurance.com/policies",
    },

    "login": {
        "method": "email_pin",
        "flow": [
            "Navigate to app.obieinsurance.com",
            "Enter email address",
            "Obie sends a 6-digit PIN to the email",
            "PIN input uses 6 SEPARATE input fields (one per digit)",
            "Must fill each field individually using browser_run_code",
        ],
        "fields": {
            "email": {"hint": "Email input on login page"},
        },
        "pin_entry": {
            "field_count": 6,
            "selector": '[data-testid="keypad-input-element"]',
            "method": "Use browser_run_code: locate all input fields, fill each digit separately",
        },
        "post_submit_wait": 5,
        "success_indicator": "url contains '/policies' or shows policy list",
    },

    "mfa": {
        "likelihood": "always",
        "methods": ["email_pin"],
        "code_length": 6,
        "notes": "PIN is the login method itself — no separate password, just email + PIN every time.",
    },

    "documents": [
        {
            "type": "Full Insurance Policy",
            "name_pattern": "{year} Full Insurance Policy - Obie - {property_name} - {policy_number}.pdf",
            "location_hint": "Policy detail page, look for 'Full Policy' or 'View Policy' download link",
            "download_method": "direct_link",
        },
        {
            "type": "Insurance Declaration",
            "name_pattern": "{year} Insurance Declaration - Obie - {property_name} - ${premium} Premium.pdf",
            "location_hint": "Policy detail page, look for 'Declaration' download link",
            "download_method": "direct_link",
        },
    ],

    "quirks": [
        "No password — login is email + 6-digit PIN sent to email every time",
        "PIN input fields are individual digit inputs with data-testid='keypad-input-element'",
        "Cannot use browser_type for PIN — must use browser_run_code to fill each field separately",
        "Policy page shows both current and past policies — check policy dates/numbers",
        "Policy number format: OAN024977-00 (original), OAN024977-01 (renewal)",
        "Each policy period (year) has separate full policy + declaration documents",
        "Documents download as direct PDF links from the policy detail page",
    ],

    "verified": "2026-04-06",
    "verified_actions": ["login_with_email_pin", "navigate_policies", "download_policy", "download_declaration"],
}
