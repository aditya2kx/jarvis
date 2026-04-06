#!/usr/bin/env python3
"""InvestorCafe (Yardi) navigation module — K-1s and distribution reports for RE partnerships.

Used by BCGK and other real estate fund managers via investorcafe.app.
Verified working 2026-04-06. Login + K-1 download + transaction export tested.
"""

PORTAL_CONFIG = {
    "name": "InvestorCafe (Yardi)",
    "keychain_service_pattern": "jarvis-investorcafe-{fund_code}",
    "login_required": True,

    "urls": {
        "login_pattern": "https://{fund_code}.investorcafe.app",
        "documents": "https://{fund_code}.investorcafe.app/content2/documents",
        "transactions": "https://{fund_code}.investorcafe.app/content2/transactions",
        "portfolio": "https://{fund_code}.investorcafe.app/content2/portfolio",
    },

    "login": {
        "method": "oauth_redirect",
        "flow": [
            "Navigate to fund_code.investorcafe.app",
            "Redirected to Yardi OAuth login page",
            "Enter email and password",
            "May redirect back or show 2FA",
        ],
        "fields": {
            "email": {"hint": "Email input on OAuth page"},
            "password": {"hint": "Password input on OAuth page"},
        },
        "post_submit_wait": 5,
        "success_indicator": "url contains '/content2' or shows investor portal nav",
    },

    "mfa": {
        "likelihood": "always",
        "methods": ["email", "sms", "voice"],
        "preferred": "email",
        "code_length": 7,
        "flow": [
            "After password, shows 'Two-Factor Authentication Required'",
            "Timer counts down (usually 60-120 seconds)",
            "Code sent to email/phone automatically",
            "7 individual digit input fields — must fill each separately",
            "If timer expires, resend options appear (Text, Voice, Email buttons)",
        ],
        "input_method": "individual_digits",
        "input_selector": "input fields in the 2FA form (7 separate inputs)",
        "fill_code": "Use browser_run_code to iterate input fields and fill each digit",
    },

    "documents": [
        {
            "type": "K-1",
            "name_pattern": "{year} K-1 - {entity_name}.pdf",
            "location_hint": "Documents section, listed by year",
            "download_method": "viewer_download_button",
            "notes": "Clicking doc name opens a document viewer/preview. Must click the Download button within the viewer to trigger actual PDF download.",
        },
        {
            "type": "Preferred Return Distributions",
            "name_pattern": "{year} {entity_name} - Preferred Return Distributions - ${total} Total.xlsx",
            "location_hint": "Transactions page — shows all distributions",
            "download_method": "export_to_excel",
            "notes": "Use 'Export to Excel' button on Transactions page. Exports ALL transactions, not filtered by year. File is Transaction_Report.xlsx.",
        },
    ],

    "quirks": [
        "Site is finicky — after login, may show empty main area. Refresh 1-2 times to load content",
        "OAuth login sometimes returns 403 on first attempt — retry works",
        "Nav buttons: Home, Portfolio, Transactions, Documents, Investment Opportunities, Upload Documents, Contact Us",
        "Documents page uses Yardi document viewer — clicking opens preview, NOT direct download",
        "To download from viewer: use browser_run_code to find and click Download button, may be inside a frame",
        "Transaction page has filter fields (Investor, Entity, Investment, Type, Date) and Export to Excel button",
        "Export to Excel downloads ALL transactions across all years — filter by date if needed",
        "Each fund has its own subdomain: {fund_code}.investorcafe.app",
        "Credentials are per-fund (same email/password may work across funds but stored separately)",
    ],

    "verified": "2026-04-06",
    "verified_actions": ["login", "2fa_email", "download_k1", "export_transactions"],
}
