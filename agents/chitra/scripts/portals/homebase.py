#!/usr/bin/env python3
"""Homebase (payroll) navigation module — structured config for tax documents.

joinhomebase.com payroll / tax documents. The `verified` block reflects a real
session (Palmetto Superfoods); password lives in macOS keychain
(`jarvis-homebase`), not in this file.
"""

PORTAL_CONFIG = {
    "name": "Homebase",
    "keychain_service": "jarvis-homebase",
    "login_required": True,

    "urls": {
        "login": "https://app.joinhomebase.com/accounts/sign_in",
        "tax_docs": "https://app.joinhomebase.com/payroll/tax_documents",
        "payroll": "https://app.joinhomebase.com/payroll",
    },

    "login": {
        "method": "form",
        "flow": [
            "Open login URL (sign_in path with underscore)",
            "Enter email and password (email for Palmetto workspace: see verified_context)",
            "Submit sign-in; expect SMS MFA before dashboard",
        ],
        "quirks": [
            "Login path is /accounts/sign_in (underscore), not sign-in",
            "May redirect to business selection if user has multiple locations — select Palmetto Superfoods",
        ],
        "fields": {
            "username": {"hint": "Email address input", "context": "main page"},
            "password": {"hint": "Password input", "context": "main page"},
        },
        "submit": {"hint": "Sign in button", "context": "main page"},
        "post_submit_wait": 8,
        "success_indicator": "url contains '/dashboard' or shows business name after MFA",
    },

    "mfa": {
        "likelihood": "conditional",
        "methods": ["sms"],
        "notes": "SMS MFA to phone number ending in 0038; enter 6-digit code when prompted.",
    },

    "navigation": {
        "tax_documents": [
            "After login, open Payroll (main app area)",
            "Open Tax Documents section under Payroll",
            "Direct PDF links on payroll tax documents page — no separate viewer step required",
        ],
    },

    "verified_context": {
        "business_entity": "Palmetto Superfoods",
        "login_email": "adi@mypalmetto.co",
        "login_email_source": "Palmetto Chrome Passwords CSV — business email, not personal",
    },

    "documents": [
        {
            "type": "W-2",
            "name_pattern": "W-2 Employee Wage Statement - {year}",
            "location_hint": "Payroll → Tax Documents → employee W-2s",
            "per_account": True,
            "download_format": "PDF",
            "download_method": "direct_pdf_url",
            "download_hint": "Direct PDF link on tax documents page (verified: Lindsay)",
            "availability": "January 31",
            "notes": "One W-2 per employee — download all needed; verified employee name: Lindsay",
        },
        {
            "type": "W-3",
            "name_pattern": "W-3 Transmittal of Wage Statements - {year}",
            "location_hint": "Payroll → Tax Documents",
            "per_account": False,
            "download_format": "PDF",
            "download_method": "direct_pdf_url",
            "download_hint": "Direct PDF link on tax documents page",
            "availability": "January 31",
        },
        {
            "type": "Form 941",
            "name_pattern": "Form 941 Quarterly Federal Tax Return - Q{quarter} {year}",
            "location_hint": "Payroll → Tax Documents → quarterly employer filings",
            "per_account": False,
            "download_format": "PDF",
            "download_method": "direct_pdf_url",
            "download_hint": "Direct PDF link per quarter (verified: Q4 Employer Quarterly Tax)",
            "availability": "End of month following quarter (Apr 30, Jul 31, Oct 31, Jan 31)",
            "notes": "Four per year (Q1–Q4). Verified download: Form 941 Q4 (Employer Quarterly Tax).",
        },
        {
            "type": "Form 940",
            "name_pattern": "Form 940 Annual Federal Unemployment Tax - {year}",
            "location_hint": "Payroll → Tax Documents → annual forms",
            "per_account": False,
            "download_format": "PDF",
            "download_method": "direct_pdf_url",
            "download_hint": "Direct PDF link (verified: Annual FUTA)",
            "availability": "January 31",
        },
    ],

    "account_selector": {
        "exists": True,
        "hint": "Business/location selector if multiple locations exist",
        "note": "Select Palmetto Superfoods (verified entity) or each location as needed for tax docs",
        "wait_after_switch": 3,
    },

    "quirks": [
        "Tax documents: Payroll → Tax Documents (not only top-level nav)",
        "Form 941: four per year (Q1–Q4); download each quarter needed",
        "Downloads are direct PDF links from the payroll tax documents page",
        "If multiple locations, each may have separate tax docs — confirm entity",
        "W-2s are per employee — collect every required employee W-2",
        "Homebase may also surface state-specific tax forms (SUI, etc.)",
        "Year-end forms (W-2, W-3, 940) typically by January 31; quarterly 941s after quarter close",
    ],

    "logout": {
        "url": "https://app.joinhomebase.com/accounts/sign-out",
        "confirm_text": "",
    },

    "verified": "2026-04-05",
    "verified_actions": [
        "login",
        "mfa_sms",
        "navigate_payroll_tax_docs",
        "download_941",
        "download_940",
        "download_w2",
        "download_w3",
    ],
}
