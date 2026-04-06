#!/usr/bin/env python3
"""InvPortal navigation module — K-1 documents for RE partnerships (MH Capital and similar).

Yardi InvPortal platform. Same viewer-based download as InvestorCafe.
Verified working 2026-04-06. Login + K-1 download tested.
"""

PORTAL_CONFIG = {
    "name": "InvPortal (Yardi)",
    "keychain_service_pattern": "jarvis-{fund_slug}",
    "login_required": True,

    "urls": {
        "login_pattern": "https://{fund_slug}.invportal.com",
        "documents": "https://{fund_slug}.invportal.com/content2/documents",
    },

    "login": {
        "method": "form",
        "fields": {
            "email": {"hint": "Email input"},
            "password": {"hint": "Password input"},
        },
        "post_submit_wait": 5,
        "success_indicator": "url contains '/content2' or shows investor portal nav",
    },

    "mfa": {
        "likelihood": "none",
        "notes": "No MFA observed for mhcapital.invportal.com login.",
    },

    "documents": [
        {
            "type": "K-1",
            "name_pattern": "{year} K-1 - {entity_name} - {investor_name}.pdf",
            "location_hint": "Documents section, click document name to open viewer",
            "download_method": "viewer_download_button",
            "notes": "Same as InvestorCafe — clicking document opens Yardi viewer, must click Download button in viewer. Use browser_run_code to find button.",
        },
    ],

    "quirks": [
        "Same Yardi platform as InvestorCafe but uses invportal.com domain",
        "Document viewer is identical — click doc name, then Download button in viewer",
        "Downloaded file may be named 'document.pdf' — rename based on registry",
        "Each fund manager has own subdomain: mhcapital.invportal.com, etc.",
    ],

    "verified": "2026-04-06",
    "verified_actions": ["login", "navigate_documents", "download_k1_from_viewer"],
}
