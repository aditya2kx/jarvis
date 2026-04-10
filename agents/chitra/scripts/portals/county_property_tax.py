#!/usr/bin/env python3
"""County property tax navigation module — structured config.

Generic navigation logic for US county appraisal district (CAD) and tax
assessor websites. Works for any county. Contains zero user-specific data.

Most counties have two separate sites:
1. Appraisal District (CAD) — property values, exemptions, ownership records
2. Tax Assessor/Collector — tax bills, payment history, receipts

This module handles both with a parameterized config. The AI agent fills in
the county-specific URLs at runtime based on the user's address.

Fort Bend County, TX is documented below as a verified example (see
FORT_BEND_TX_VERIFIED).
"""

PORTAL_CONFIG = {
    "name": "County Property Tax",
    "keychain_service": None,
    "login_required": False,

    "urls": {
        # These are patterns — the AI fills in the actual county name at runtime
        # Texas pattern: esearch.{county}cad.org, {county}tax.org
        # California pattern: varies by county assessor
        "cad_search": "https://esearch.{county}cad.org/",
        "tax_assessor": "https://www.{county}tax.org/",
    },

    "login": {
        "method": "none",
        "quirks": ["Property data is public record — no login required"],
    },

    "mfa": {
        "likelihood": "never",
    },

    "search": {
        "method": "address",
        "fields": {
            "street_number": "Street Number field (just the number, e.g. '1234')",
            "street_name": "Street Name field (just the name, e.g. 'Main')",
        },
        "submit_hint": "Search button",
        "results_hint": "Results table with columns: Property ID | Geo ID | Type | Owner | Address | Appraised Value",
        "select_hint": "Click matching property row to see detail page",
    },

    "documents": [
        {
            "type": "Property Tax Bill",
            "name_pattern": "Property Tax Bill - {county} County - {year}",
            "location_hint": "Tax Assessor site → search by Property ID or address → Tax Statement",
            "per_account": False,
            "download_format": "PDF",
            "download_hint": "Download or Print tax statement link",
            "availability": "October-November (bills), January-March (receipts)",
        },
        {
            "type": "Homestead Exemption",
            "name_pattern": "Homestead Exemption - {county} County - {year}",
            "location_hint": "CAD property detail page → Exemptions section",
            "per_account": False,
            "download_format": "HTML",
            "download_hint": "Screenshot or extract from property detail page (usually not downloadable)",
            "availability": "Year-round (reflects current exemption status)",
        },
    ],

    "data_to_extract": {
        "account_info": [
            "Property ID / Quick Ref ID",
            "Geographic ID",
            "Property Type (Residential, Commercial)",
        ],
        "location": [
            "Situs Address",
            "Legal Description",
            "Subdivision",
        ],
        "ownership": [
            "Owner name(s)",
            "Ownership percentage",
            "Mailing address",
        ],
        "values": [
            "Improvement Homesite Value",
            "Land Homesite Value",
            "Market Value (total)",
            "Appraised Value",
            "Value history (multi-year table)",
        ],
        "exemptions": [
            "Homestead (HS)",
            "Over-65",
            "Disability",
            "Other exemptions",
        ],
        "taxing_jurisdictions": [
            "Entity | Description | Market Value | Taxable Value",
            "Common: County, City, ISD, MUD, Drainage, Parks",
        ],
        "deed_history": [
            "Deed dates, types, grantor/grantee",
            "Useful for confirming purchase date",
        ],
    },

    "county_url_patterns": {
        "texas_cad": "https://esearch.{county}cad.org/",
        "texas_tax": "https://www.{county}tax.org/",
        "texas_propaccess": "https://propaccess.{county}cad.org/",
        "california_assessor": "https://www.{county}county.gov/assessor/",
        "bis_consultants": "https://esearch.{county}cad.org/",
    },

    "address_to_county_method": (
        "Given a street address, determine the county via: "
        "1) census.gov geocoder API, 2) USPS ZIP lookup, or "
        "3) direct web search for '{city} {state} county'. "
        "Then construct the CAD/tax URLs from county_url_patterns."
    ),

    "quirks": [
        "County .gov main sites often use Cloudflare bot protection (403 errors)",
        "CAD search sites (.org) are typically less protected and work with Playwright",
        "BIS Consultants sites (common in TX) have consistent UI across counties",
        "California uses county-specific assessor sites — less standardized",
        "Address search is the most reliable lookup method (better than owner name)",
        "Multi-county properties are rare but possible — check all applicable counties",
        "New construction may not appear in CAD until next assessment cycle",
        "Homestead exemptions filed mid-year may not show until next year's tax roll",
        "Some tax assessor sites require a free account to download tax bills as PDF",
        "Tax bills vs receipts: bills available Oct-Nov, receipts after Jan payment deadline",
        "Texas: many counties use ACT (acttax.com) webdev portals for lookup, "
        "statements, and payment — often no login (see FORT_BEND_TX_VERIFIED)",
    ],

    "logout": {
        "url": None,
        "confirm_text": None,
    },
}

# --- Verified: Fort Bend County, TX (primary residence workflow) ----------------
# Last field-tested alignment: 2026-04-05. No login; public records only.

FORT_BEND_TX_VERIFIED = {
    "county": "Fort Bend County, Texas",
    "verified": "2026-04-05",
    "verified_actions": [
        "search_by_account",
        "download_tax_statement",
        "download_tax_receipt",
        "fbcad_appraisal_notice",
    ],
    "login_required": False,
    "notes": (
        "Tax Assessor-Collector hub (policies, contacts, links to tools): "
        "department landing page. Statements and receipts come from the online "
        "tax lookup / payment portal (ACT), not from a login-gated account."
    ),
    "urls": {
        "tax_assessor_department": (
            "https://www.fortbendcountytx.gov/government/departments/"
            "tax-assessor-collector"
        ),
        "property_tax_database": (
            "https://www.fortbendcountytx.gov/government/departments/"
            "tax-assessor-collector/property-tax-database"
        ),
        # Online search, statements, and payment (verified path for bills/receipts)
        "tax_payment_lookup_act": (
            "https://actweb.acttax.com/act_webdev/fbc/index.jsp"
        ),
        "fbcad_property_search": "https://esearch.fbcad.org/",
    },
    "example_property": {
        "property_account": "8118640020010907",
        "cad_quick_ref": "R555090",
        "situs_address": "1414 Crown Forest Dr",
    },
    "documents_observed": [
        {
            "type": "Tax Statement",
            "source": "tax_payment_lookup_act",
            "format": "PDF",
        },
        {
            "type": "Tax Receipt",
            "source": "tax_payment_lookup_act",
            "format": "PDF",
            "note": "Verified: shows $9,757 PAID",
        },
        {
            "type": "Appraisal Notice",
            "source": "fbcad_property_search",
            "format": "PDF",
            "note": (
                "2025 and 2026 notices downloaded; HS (Homestead) exemption "
                "shown active on notices"
            ),
        },
    ],
    "verified_steps": [
        "Open tax_assessor_department or property_tax_database for context; "
        "use tax_payment_lookup_act for account search, tax statement, and receipt.",
        "Search by property account number (or address as offered on the portal).",
        "Download Tax Statement PDF from the lookup results / document links.",
        "Download Tax Receipt PDF (post-payment) from the same portal flow.",
        "For appraisal (value, exemptions, notice PDFs): open fbcad_property_search, "
        "find the parcel, and use direct PDF links on the public property record "
        "(appraisal notices available as direct PDFs from search results / detail).",
    ],
    "hosting_quirk": (
        "Some Fort Bend-generated PDFs are served via bisfiles.co (third-party "
        "document host); treat as normal download targets when links appear on "
        "official county/CAD pages."
    ),
}
