"""
Just Appraised — Taxpayer Portal (property appraisal protests & homestead docs)
Uses Auth0-based login via auth.justappraised.com.
IMPORTANT: Auth0 fails in Cursor's Electron browser — MUST use Playwright Chrome.
"""

PORTAL_CONFIG = {
    "name": "Just Appraised (Taxpayer Portal)",
    "keychain_service": "jarvis-justappraised",
    "login_required": True,
    "urls": {
        "home": "https://taxpayer.justappraised.com",
        "county_select": "https://taxpayer.justappraised.com/county-select",
        "dashboard": "https://taxpayer.justappraised.com/dashboard",
        "application": "https://taxpayer.justappraised.com/application/{app_id}",
    },
    "login": {
        "method": "auth0_redirect",
        "flow": [
            "Navigate to taxpayer.justappraised.com — auto-redirects through county-select to Auth0 login",
            "Auth0 login page: enter email in 'Email address' textbox, click Continue",
            "Auth0 password page: enter password in 'Password' textbox, click Continue",
            "Redirects to /dashboard showing 'Forms - Just Appraised' with county nav",
        ],
        "fields": {
            "email": {"selector": "textbox 'Email address'", "type": "email"},
            "password": {"selector": "textbox 'Password'", "type": "password"},
        },
        "submit_each_step": "button 'Continue'",
        "post_submit_wait": 3,
        "success_indicator": "url contains '/dashboard' and page title is 'Forms - Just Appraised'",
    },
    "mfa": {
        "likelihood": "none",
        "notes": "No MFA observed during verified login on 2026-04-06.",
    },
    "documents": [
        {
            "type": "Homestead Exemption Application (Form 50-114)",
            "name_pattern": "{year} Texas Form 50-114 - Homestead Exemption Application.pdf",
            "location_hint": "Dashboard > Exemptions > Submitted Forms > View Application > 'Generated Documents' tab",
            "download_method": "s3_signed_url",
            "download_flow": [
                "On dashboard, find 'Exemptions' section with 'Submitted Forms'",
                "Click 'View Application' button on the submitted form row",
                "Click 'Generated Documents' tab (5th tab)",
                "Find 'TEXAS_FORM_50_114_HS' entry under 'Official Document PDFs'",
                "Click 'View' link — opens S3 signed URL in new tab",
                "Use browser_run_code with fetch() to download PDF bytes (inline PDF viewer)",
                "Save via data: URL download trigger",
            ],
            "notes": "PDF is hosted on ja-file-uploads.s3.amazonaws.com with time-limited signed URL. The View link opens inline PDF — use fetch-based download (same pattern as Ziprent).",
        },
    ],
    "quirks": [
        "CRITICAL: Auth0 login FAILS in Cursor Electron browser ('sent an invalid response'). MUST use Playwright Chrome (user-playwright MCP).",
        "Navigate to taxpayer.justappraised.com — it auto-redirects through county-select to auth. No need to manually select county.",
        "If county-select page appears (e.g., in Cursor browser), buttons are covered by overlay divs — click is intercepted. This is a non-issue in Playwright Chrome since auto-redirect skips it.",
        "Dashboard shows all form types: Exemptions (HS, Disabled Veteran), BPP Rendition",
        "Submitted forms show: filing date, application number (#27782044), property ref (R555090), status (Application Processed)",
        "Application detail has 5 tabs: Exemptions Requested, Applicant Information, Homestead Information, File Uploads, Generated Documents",
        "Generated Documents tab has the official PDF with S3 signed download URL",
        "accessiBe accessibility widget loads on every page — ignore it",
    ],
    "verified": "2026-04-06",
    "verified_actions": ["login", "navigate_dashboard", "view_application", "download_form_50_114"],
}
