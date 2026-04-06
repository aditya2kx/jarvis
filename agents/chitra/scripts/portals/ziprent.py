#!/usr/bin/env python3
"""Ziprent navigation module — property management portal for rental 1099-MISC.

Verified working 2026-04-06. Login + tax form download tested end-to-end.
"""

PORTAL_CONFIG = {
    "name": "Ziprent",
    "keychain_service": "jarvis-ziprent",
    "login_required": True,

    "urls": {
        "login": "https://app.ziprent.com/auth/login",
        "tax_forms": "https://app.ziprent.com/auth/owner/tax-forms",
        "rents": "https://app.ziprent.com/auth/owner/rents/{property_id}",
        "invoices": "https://app.ziprent.com/auth/owner/invoices/{property_id}",
        "dashboard": "https://app.ziprent.com/auth/owner/properties/{property_id}",
    },

    "login": {
        "method": "form",
        "fields": {
            "email": {"selector": "textbox near 'Email'", "type": "email"},
            "password": {"selector": "textbox near 'Password'", "type": "password"},
        },
        "submit": {"selector": "button ' Login'"},
        "post_submit_wait": 3,
        "success_indicator": "url contains '/auth/owner/properties/'",
    },

    "mfa": {
        "likelihood": "none",
        "notes": "No MFA observed during login.",
    },

    "documents": [
        {
            "type": "1099-MISC",
            "name_pattern": "{year} Form 1099-MISC - Ziprent - {property_address} Income ${amount}.pdf",
            "location_hint": "Tax Forms page (account dropdown > Tax Forms, NOT in main nav)",
            "download_method": "direct_link",
            "download_url_pattern": "/auth/owner/tax-form/download/{form_id}",
            "availability": "January 31",
            "notes": "Download link opens inline PDF in new tab. Use fetch() with credentials to get actual PDF bytes, then create data: URL download.",
        },
    ],

    "quirks": [
        "Login URL is /auth/login (NOT /login which redirects to WordPress wp-login.php)",
        "Main site ziprent.com and app.ziprent.com are different — app subdomain is the portal",
        "Tax Forms is NOT in the left sidebar nav — it's under the account email dropdown menu (top right)",
        "After login, redirects to first property dashboard automatically",
        "Download link returns inline PDF (Content-Disposition: inline). Browser PDF viewer wraps it in HTML. Use page.evaluate(fetch()) to get raw PDF bytes.",
        "Chrome Passwords CSV may store a password-reset URL, not the login URL — always use /auth/login",
        "Multiple years of 1099 forms shown in a table — download the 'Original' type for the desired year",
        "reCAPTCHA iframe present on login page but did not block automated login",
    ],

    "verified": "2026-04-06",
    "verified_actions": ["login", "navigate_to_tax_forms", "download_1099"],
}
