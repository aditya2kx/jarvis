#!/usr/bin/env python3
"""E*Trade / Morgan Stanley navigation module — structured config.

Generic navigation logic for us.etrade.com. Contains zero user-specific data.
Any E*Trade account holder can use this module as-is.
"""

PORTAL_CONFIG = {
    "name": "E*Trade",
    "keychain_service": "jarvis-etrade",
    "login_required": True,

    "urls": {
        "login": "https://us.etrade.com/etx/hw/auth",
        "tax_docs": "https://us.etrade.com/etx/pxy/my-account/documents",
        "stock_plan": "https://us.etrade.com/etx/sp/stockplan#/myAccount/taxDocuments",
        "logout": "https://us.etrade.com/etx/pxy/logout",
    },

    "login": {
        "method": "form",
        "quirks": [
            "Login form is NOT in an iframe (unlike Schwab) — standard page elements",
            "Redirects to us.etrade.com/etx/pxy/login with TARGET param",
            "A 'Use security code' checkbox exists — leave unchecked",
            "A loading dialog appears briefly after clicking Log on",
        ],
        "fields": {
            "username": {"hint": "User ID textbox", "context": "main page"},
            "password": {"hint": "Password textbox", "context": "main page"},
        },
        "submit": {"hint": "Log on button", "context": "main page"},
        "post_submit_wait": 10,
        "success_indicator": "url contains account overview or dashboard",
    },

    "mfa": {
        "likelihood": "always",
        "methods": ["sms"],
        "preferred": "sms",
        "device_trust": True,
        "trigger_hint": "Page with 'Help us confirm your identity' + masked phone number",
        "flow": [
            "Redirects to /login/sendotpcode",
            "Shows masked phone: +1-XXX-XXX-XXXX",
            "'verify another way' only lets you enter a DIFFERENT phone number (no email)",
            "Click 'Send Code' button",
            "Redirects to /login/verifyotpcode",
            "Enter code in 'Enter verification code' textbox",
            "'Save this device?' radio → select 'Yes, save this device.'",
            "Click 'Submit' button",
        ],
        "notes": (
            "MFA is mandatory on new devices — no way to skip it. "
            "SMS is the only option (no email, no authenticator app). "
            "'verify another way' is misleading — just lets you enter a different phone."
        ),
    },

    "documents": [
        {
            "type": "1099",
            "name_pattern": "Consolidated 1099 - {year}",
            "location_hint": "Tax Documents section under Documents",
            "per_account": False,
            "download_format": "PDF",
            "download_hint": "Download or View button next to the 1099 entry",
            "availability": "mid-February",
        },
        {
            "type": "Stock Plan Supplement",
            "name_pattern": "Stock Plan Transaction Supplement - {year}",
            "location_hint": "Stock Plan tab → Tax Documents",
            "per_account": False,
            "download_format": "PDF",
            "download_hint": "Download link in Stock Plan section",
            "availability": "mid-February",
        },
        {
            "type": "Form 3921",
            "name_pattern": "Form 3921 - Exercise of ISO - {year}",
            "location_hint": "Stock Plan tab → Tax Documents (if ISOs exercised)",
            "per_account": False,
            "download_format": "PDF",
            "download_hint": "Download link",
            "availability": "January 31",
            "conditional": "Only if ISO exercises occurred during the year",
        },
    ],

    "account_selector": {
        "exists": False,
        "note": "Brokerage and stock plan are separate URL sections, not a dropdown",
    },

    "quirks": [
        "MFA is mandatory on new devices — absolutely no way to bypass",
        "SMS is the ONLY MFA option — no email, no authenticator app",
        "'verify another way' is misleading — only offers entering a different phone number",
        "Login flow uses multiple page redirects (not a SPA)",
        "If 'save this device' was selected, future logins may skip MFA",
        "Footer mentions 'Need tax documents for a closed account?' — old creds may work",
        "Stock plan documents are in a separate section from brokerage 1099s",
    ],

    "logout": {
        "url": "https://us.etrade.com/etx/pxy/logout",
        "confirm_text": "",
    },
}
