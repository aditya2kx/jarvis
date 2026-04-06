#!/usr/bin/env python3
"""Chase navigation module — structured config for tax document retrieval.

Generic navigation logic for chase.com. Contains zero user-specific data.
Covers: mortgage (Form 1098), banking (1099-INT), credit cards.
"""

PORTAL_CONFIG = {
    "name": "JPMorgan Chase",
    "keychain_service": "jarvis-chase",
    "login_required": True,

    "urls": {
        "login": "https://secure01c.chase.com/web/auth/#/logon/logon/chaseOnline",
        "tax_docs": "https://secure.chase.com/web/auth/dashboard#/dashboard/documents/tax",
        "statements": "https://secure.chase.com/web/auth/dashboard#/dashboard/documents",
        "summary": "https://secure.chase.com/web/auth/dashboard#/dashboard/overview",
        "logout": "https://secure.chase.com/web/auth/#/logon/logon/signoff",
    },

    "login": {
        "method": "form",
        "quirks": [
            "Login form URL has a hash-based SPA route (#/logon/logon/chaseOnline)",
            "Username and password are on the same page",
            "Chase may show a 'Welcome back' interstitial with account previews",
        ],
        "fields": {
            "username": {"hint": "User ID input field", "context": "main page"},
            "password": {"hint": "Password input field", "context": "main page"},
        },
        "submit": {"hint": "Sign in button", "context": "main page"},
        "post_submit_wait": 10,
        "success_indicator": "url contains 'dashboard' or shows account overview",
    },

    "mfa": {
        "likelihood": "always",
        "methods": ["mobile_app_push", "sms", "phone_call", "email"],
        "preferred": "mobile_app_push",
        "device_trust": True,
        "trigger_hint": "Identity verification page: 'We don't recognize this device'",
        "flow": [
            "Shows 'We don't recognize this device or location'",
            "Default is Chase mobile app push notification",
            "User approves on their phone — wait for page to auto-advance",
            "If expired, 'Get another notification' link appears — click it",
            "Alternative: select text, call, or email for code-based verification",
            "'Remember this device' — check the box if available",
        ],
        "notes": "In practice, Chase defaulted to mobile app push notification, not email/SMS. User must approve on Chase mobile app. If approval times out, click 'Get another notification'.",
    },

    "documents": [
        {
            "type": "Form 1098",
            "name_pattern": "Form 1098 Mortgage Interest Statement - {year}",
            "location_hint": "Tax Documents page under Mortgage section",
            "per_account": True,
            "download_format": "PDF",
            "download_hint": "Download PDF link",
            "availability": "January 31",
        },
        {
            "type": "1099-INT",
            "name_pattern": "Form 1099-INT Interest Income - {year}",
            "location_hint": "Tax Documents page under Banking section",
            "per_account": True,
            "download_format": "PDF",
            "download_hint": "Download link",
            "availability": "January 31",
            "conditional": "Only if interest earned > $10",
        },
    ],

    "account_selector": {
        "exists": True,
        "hint": "Tax documents page shows forms grouped by account (checking, mortgage, credit card)",
        "note": "Usually pre-listed — no need to switch, all accounts visible",
        "wait_after_switch": 3,
    },

    "quirks": [
        "Chase uses hash-based SPA routing — URLs look like #/dashboard/documents/tax",
        "Tax documents page may take a few seconds to populate after navigation",
        "Mortgage 1098 is the primary tax doc for most users",
        "Chase may show promotional interstitials ('Learn about our new features')",
        "Multiple mortgages appear as separate sections",
        "Chase has aggressive bot detection — may flag automated browsers",
        "Session expiry is relatively short; re-login may be needed",
        "Chase supports email-based MFA which is useful for automation",
    ],

    "logout": {
        "url": "https://secure.chase.com/web/auth/#/logon/logon/signoff",
        "confirm_text": "You have been signed off",
    },

    "verified": "2026-04-06",
    "verified_actions": ["login", "mfa_mobile_push", "navigate_tax_docs", "download_1098"],
}
