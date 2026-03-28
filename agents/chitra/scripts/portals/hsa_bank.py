#!/usr/bin/env python3
"""HSA provider navigation module — structured config for tax document retrieval.

Generic navigation patterns for common HSA providers. Contains zero user-specific data.
The specific provider (HealthEquity, Fidelity HSA, Optum Bank, HSA Bank, Lively, etc.)
varies by employer — this module covers common patterns across providers.
"""

PORTAL_CONFIG = {
    "name": "HSA Provider",
    "keychain_service": "jarvis-hsa",
    "login_required": True,

    "urls": {
        # Common HSA provider login URLs — the AI picks the right one based on the user's provider
        "healthequity": "https://my.healthequity.com/",
        "optum_bank": "https://mycdh.optum.com/",
        "fidelity_hsa": "https://digital.fidelity.com/prgw/digital/login/full-page",
        "hsa_bank": "https://myaccounts.hsabank.com/",
        "lively": "https://app.livelyme.com/login",
    },

    "login": {
        "method": "form",
        "quirks": [
            "Each HSA provider has a different login page layout",
            "Some (HealthEquity) use employer SSO integration",
            "The AI should ask the user which HSA provider they use during onboarding",
        ],
        "fields": {
            "username": {"hint": "Username / Email input", "context": "varies by provider"},
            "password": {"hint": "Password input", "context": "varies by provider"},
        },
        "submit": {"hint": "Sign In / Log In button", "context": "varies"},
        "post_submit_wait": 8,
        "success_indicator": "dashboard or account overview page",
    },

    "mfa": {
        "likelihood": "conditional",
        "methods": ["sms", "email"],
        "preferred": "email",
        "device_trust": True,
        "trigger_hint": "Varies by provider — usually a verification code page",
    },

    "documents": [
        {
            "type": "Form 5498-SA",
            "name_pattern": "Form 5498-SA HSA Contributions - {year}",
            "location_hint": "Tax Documents or Tax Forms section",
            "per_account": False,
            "download_format": "PDF",
            "download_hint": "Download or View link",
            "availability": "May (deadline is May 31)",
            "notes": "Reports total HSA contributions for the year — needed for Form 8889",
        },
        {
            "type": "Form 1099-SA",
            "name_pattern": "Form 1099-SA HSA Distributions - {year}",
            "location_hint": "Tax Documents section",
            "per_account": False,
            "download_format": "PDF",
            "download_hint": "Download link",
            "availability": "January 31",
            "conditional": "Only if HSA distributions (withdrawals) were made during the year",
            "notes": "Reports distributions — qualified medical expenses are tax-free",
        },
    ],

    "account_selector": {
        "exists": False,
        "note": "Typically one HSA account per provider",
    },

    "quirks": [
        "HSA provider varies by employer — ask during onboarding 'Who is your HSA custodian?'",
        "Form 5498-SA arrives in MAY — well after the April filing deadline",
        "If no distributions, there may be NO Form 1099-SA (only 5498-SA for contributions)",
        "HealthEquity is the most common employer-linked HSA (used by large tech companies)",
        "Fidelity HSA is accessed through the same Fidelity login (see fidelity.py module)",
        "Some employers contribute to HSA — those show on the 5498-SA as employer contributions",
        "HSA contribution limits: $4,300 individual / $8,550 family (2025 limits)",
    ],

    "logout": {
        "url": None,
        "confirm_text": None,
    },
}
