#!/usr/bin/env python3
"""E*Trade / Morgan Stanley navigation module — structured config.

Generic navigation logic for us.etrade.com. Credential identity lives in Keychain only;
optional verified_* fields record one observed run for automation regression hints.
"""

PORTAL_CONFIG = {
    "name": "E*Trade",
    "keychain_service": "jarvis-etrade",
    "login_required": True,

    "urls": {
        "login": "https://us.etrade.com/e/t/user/login",
        # Post-login: Tax Center holds 1099s and notices; may also be reachable from main nav.
        "tax_center": "https://us.etrade.com/etx/pxy/my-account/documents",
        "tax_docs": "https://us.etrade.com/etx/pxy/my-account/documents",
        "stock_plan": "https://us.etrade.com/etx/sp/stockplan#/myAccount/taxDocuments",
        "logout": "https://us.etrade.com/etx/pxy/logout",
    },

    "login": {
        "method": "form",
        "quirks": [
            "Start at /e/t/user/login (verified entry URL)",
            "Login form is NOT in an iframe (unlike Schwab) — standard page elements",
            "May redirect through us.etrade.com/etx/pxy/login with TARGET param",
            "A 'Use security code' checkbox exists — leave unchecked unless you intend token flow",
            "A loading dialog appears briefly after clicking Log on",
        ],
        "fields": {
            "username": {"hint": "User ID textbox", "context": "main page"},
            "password": {"hint": "Password textbox", "context": "main page"},
        },
        "submit": {"hint": "Log on button", "context": "main page"},
        "post_submit_wait": 10,
        "success_indicator": "Past MFA: URL reaches account overview, dashboard, or Tax Center",
    },

    "mfa": {
        "likelihood": "always",
        "always_required": True,
        "methods": ["sms"],
        "preferred": "sms",
        "device_trust": True,
        "trigger_hint": (
            "After password submit: 'Verify your identity' page; SMS to registered phone only "
            "(no email option). Code arrives by text."
        ),
        "flow": [
            "Submit User ID + password on login page",
            "Land on 'Verify your identity' — site sends SMS to registered phone",
            "Enter 6-digit code from text in the verification code field",
            "Optional: 'Save this device?' — choose Yes if you want fewer prompts in a human browser",
            "Submit to complete MFA",
        ],
        "legacy_path_hints": [
            "Older flows may show URLs like /login/sendotpcode and /login/verifyotpcode",
            "'verify another way' only allows entering a different phone number, not email",
        ],
        "slack_automation": {
            "description": (
                "When the automation needs the SMS code, DM the user via "
                "skills.slack.adapter.send_progress() with a clear ask for the 6-digit code, "
                "then read their reply from the configured DM channel (e.g. conversations.history "
                "with oldest set to the sent message ts). request_otp() or ask_user() are alternatives "
                "if you want a single blocking helper instead of manual poll."
            ),
            "module": "skills.slack.adapter",
            "notify": "send_progress",
            "read_reply": "read_replies(channel, oldest=sent_ts) or ask_user() / request_otp()",
        },
        "notes": (
            "Verified: MFA is required for this login path; SMS only — no email MFA. "
            "'Verify another way' does not add email; it is phone-oriented."
        ),
    },

    "navigation": {
        "tax_documents": [
            "After successful login + MFA, go to Tax Center for 1099 and related tax documents",
            "URLs.tax_center (same host path as legacy tax_docs) reaches the documents hub",
            "Stock plan-specific PDFs may also appear under Stock Plan → Tax Documents",
        ],
    },

    "documents": [
        {
            "type": "1099",
            "name_pattern": "Consolidated 1099 - {year}",
            "location_hint": "Tax Center / tax documents list (not only Stock Plan tab)",
            "per_account": True,
            "download_format": "PDF",
            "download_method": "direct_pdf_url",
            "download_hint": "Each row exposes a direct PDF link on the tax documents page — fetch or click to download",
            "availability": "mid-February",
        },
        {
            "type": "Stock Plan Supplement",
            "name_pattern": "Stock Plan Transaction Supplement - {year}",
            "location_hint": "Tax Center list and/or Stock Plan tab → Tax Documents",
            "per_account": False,
            "download_format": "PDF",
            "download_method": "direct_pdf_url",
            "download_hint": "Direct PDF link on the tax documents page",
            "availability": "mid-February",
        },
        {
            "type": "Tax notices",
            "name_pattern": "Tax Statement Availability Notice, No 1099 Issued, etc.",
            "location_hint": "Same Tax Center list as 1099s — informational PDFs or notices per account",
            "per_account": True,
            "download_format": "PDF",
            "download_method": "direct_pdf_url",
            "download_hint": "Direct PDF links like other tax rows",
            "availability": "varies",
        },
        {
            "type": "Form 3921",
            "name_pattern": "Form 3921 - Exercise of ISO - {year}",
            "location_hint": "Stock Plan tab → Tax Documents (if ISOs exercised)",
            "per_account": False,
            "download_format": "PDF",
            "download_method": "direct_pdf_url",
            "download_hint": "Direct PDF link when present",
            "availability": "January 31",
            "conditional": "Only if ISO exercises occurred during the year",
        },
    ],

    "account_selector": {
        "exists": False,
        "note": "Multiple accounts show as separate rows in Tax Center (e.g. brokerage vs stock plan); not a single global dropdown",
    },

    "quirks": [
        "MFA via SMS is mandatory on verified path — plan automation to collect a 6-digit code from the user",
        "SMS is the only MFA channel observed — no email option on the verify page",
        "Login uses multiple redirects (not a single SPA)",
        "Tax Center rows can include: full 1099 packages, supplements, availability notices, and 'no 1099 issued' (e.g. below threshold)",
        "Footer may mention tax documents for closed accounts — old credentials may still reach historical PDFs",
    ],

    "verified": "2026-04-05",
    "verified_actions": [
        "login",
        "mfa_sms",
        "navigate_tax_center",
        "download_1099",
        "download_supplement",
    ],
    "verified_document_examples": [
        "1099 Consolidated — DoorDash stock plan account ending 0060",
        "Stock Plan Transactions Supplement",
        "Tax Statement Availability Notice — account 0060",
        "No 1099 Issued notice — account 2946 (below threshold)",
    ],

    "logout": {
        "url": "https://us.etrade.com/etx/pxy/logout",
        "confirm_text": "",
    },
}
