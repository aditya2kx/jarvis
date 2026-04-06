"""
Just Appraised — Taxpayer Portal (property appraisal protests & homestead docs)
Uses Auth0-based login via auth.justappraised.com.
County must be selected before login redirect happens.
"""

PORTAL_CONFIG = {
    "name": "Just Appraised (Taxpayer Portal)",
    "keychain_service": "jarvis-justappraised",
    "login_required": True,
    "urls": {
        "county_select": "https://taxpayer.justappraised.com/county-select",
        "login_redirect": "https://auth.justappraised.com/u/login/identifier",
        "fort_bend_cad": "https://taxpayer.justappraised.com/fort-bend-cad",
    },
    "login": {
        "method": "auth0_redirect",
        "flow": [
            "Navigate to taxpayer.justappraised.com — redirects to /county-select",
            "Select county (Fort Bend CAD for 1414 Crown Forest Dr)",
            "Click Continue — redirects to auth.justappraised.com/u/login",
            "Enter email + password on Auth0 form",
            "Redirect back to taxpayer portal with the selected county",
        ],
        "fields": {
            "email": {"hint": "Email input on Auth0 login page"},
            "password": {"hint": "Password input on Auth0 login page"},
        },
        "post_submit_wait": 5,
        "success_indicator": "url contains '/fort-bend-cad' or shows property search",
    },
    "mfa": {
        "likelihood": "unknown",
        "notes": "Auth0 may prompt MFA depending on account settings. Not yet tested due to auth service issues.",
    },
    "county_select": {
        "method": "search_and_click",
        "flow": [
            "Type county name in search box (e.g., 'Fort Bend')",
            "County buttons appear filtered in the list",
            "Click the Fort Bend CAD button to select it",
            "Click Continue to proceed to auth",
        ],
        "known_issue": "County buttons exist in accessibility tree but are covered by overlay div elements. Standard click() is intercepted. May need JavaScript dispatchEvent or keyboard navigation.",
        "workaround": "Use the state filter dropdown to select TX first, then scroll to Fort Bend CAD. Or use keyboard Tab navigation after searching.",
    },
    "documents": [
        {
            "type": "Homestead Exemption Application (Form 50-114)",
            "name_pattern": "{year} Texas Form 50-114 - Homestead Exemption Application.pdf",
            "location_hint": "Property detail page or filings section after login",
            "download_method": "direct_link",
            "notes": "This is the application the taxpayer filed. May be under 'Filings' or 'Documents' for the property. Alternatively, the blank form is available at https://comptroller.texas.gov/taxes/property-tax/forms/50-114.pdf",
        },
        {
            "type": "Appraisal Notice",
            "name_pattern": "{year} Appraisal Notice - {county} CAD - {property_address}.pdf",
            "location_hint": "Available on public FBCAD site without login",
            "download_method": "direct_link",
            "notes": "Appraisal notices are also available on the public FBCAD site (esearch.fbcad.org) without login. The Just Appraised portal may have additional protest/filing history.",
        },
    ],
    "quirks": [
        "auth.justappraised.com uses Auth0 — requires proper OAuth state parameters in redirect URL",
        "Auth0 authorize endpoint returns 403 if called without correct client_id/state — must go through the app's redirect flow",
        "County select page is a React SPA (styled-components) — buttons exist in DOM but may be covered by overlay divs",
        "reCAPTCHA script loaded on county-select page but no visible captcha challenge observed",
        "Page JS tries to silently fetch token on load — logs 'Missing Refresh Token' error if not logged in (expected)",
        "Fort Bend CAD (Texas) — covers properties in Fort Bend County, TX including Sugar Land, Missouri City, etc.",
        "Blank Form 50-114 available from Texas Comptroller: https://comptroller.texas.gov/taxes/property-tax/forms/50-114.pdf",
        "FBCAD appraisal notices with homestead exemption status can serve as proof of exemption without this portal",
    ],
    "verified": "2026-04-06",
    "verified_actions": ["county_select_page_loads", "creds_found_in_csv"],
    "unverified": ["login", "document_download"],
    "auth_issue_log": "2026-04-06: auth.justappraised.com returns 'sent an invalid response' in automated Chromium browser. curl to /authorize returns 403. County-select page loads but auth redirect fails.",
}
