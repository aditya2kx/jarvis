#!/usr/bin/env python3
"""Derive a document registry for the next tax year from a prior-year return profile.

Given a structured prior-year profile (e.g., profile-2024.json), this script
generates the expected document checklist for the following year. It extracts
every issuer, entity, schedule, and form from the return and creates a
document entry for each expected recurring item.

This is CHITRA's core bootstrap capability — it works for any user who has
a structured prior-year profile, not just a specific taxpayer.

Usage:
    python derive_registry_from_return.py [--profile path/to/profile.json] [--target-year 2025]
    python derive_registry_from_return.py --diff path/to/existing-registry.json
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
from core.config_loader import kb_path, project_dir


def load_json(path):
    with open(path) as f:
        return json.load(f)


def derive_documents(profile, target_year):
    """Walk every section of the prior-year profile and emit expected documents."""
    docs = []
    next_id = 1
    income = profile.get("income", {})

    # W-2s from each employer
    for emp in income.get("wages", {}).get("employers", []):
        docs.append({
            "id": next_id,
            "docType": "W-2",
            "issuer": emp["name"],
            "for": emp.get("for", "Unknown"),
            "category": "Income",
            "source": "employer_portal",
            "recurring": True,
            "priorYearData": {"wages": emp.get("wages")},
        })
        next_id += 1

    # 1099s from interest sources (Schedule B)
    for src in income.get("interest", {}).get("sources", []):
        if "K-1" in src.get("payer", ""):
            continue  # K-1 interest handled under partnerships
        docs.append({
            "id": next_id,
            "docType": "1099-INT",
            "issuer": src["payer"],
            "for": "Joint",
            "category": "Income",
            "source": "brokerage_portal",
            "recurring": True,
            "priorYearData": {"amount": src.get("amount")},
        })
        next_id += 1

    # 1099-B / 1099-DIV from capital gains sources (Schedule D / 8949)
    brokers_seen = set()
    for period in ["shortTerm", "longTerm"]:
        for txn in income.get("capitalGains", {}).get(period, {}).get("transactions", []):
            broker = txn["description"].split("(")[0].strip()
            if broker in brokers_seen:
                continue
            brokers_seen.add(broker)
            docs.append({
                "id": next_id,
                "docType": "Consolidated 1099",
                "issuer": broker,
                "for": "Joint",
                "category": "Income",
                "source": "brokerage_portal",
                "recurring": True,
                "priorYearData": {"proceeds": txn.get("proceeds")},
            })
            next_id += 1

    # Schedule C — business records
    biz = income.get("businessIncome", {})
    if biz.get("businessName"):
        docs.append({
            "id": next_id,
            "docType": "Schedule C Records",
            "issuer": biz["businessName"],
            "for": "Joint",
            "category": "Income",
            "source": "user_records",
            "recurring": True,
            "priorYearData": {"grossReceipts": biz.get("grossReceipts"), "netLoss": biz.get("netLoss")},
        })
        next_id += 1

    # Schedule E — rental properties
    for prop in income.get("rentalRealEstate", {}).get("properties", []):
        address = prop.get("address", "Unknown Property")
        lender = prop.get("mortgageLender")

        docs.append({
            "id": next_id,
            "docType": "1099-MISC (Rental Income)",
            "issuer": f"Property Manager - {address}",
            "for": "Joint",
            "category": "Income",
            "source": "property_manager_portal",
            "recurring": True,
            "details": {"address": address},
            "priorYearData": {"rentReceived": prop.get("rentReceived")},
        })
        next_id += 1

        if lender:
            docs.append({
                "id": next_id,
                "docType": "Form 1098 (Mortgage Interest) - RENTAL",
                "issuer": lender,
                "for": "Joint",
                "category": "Deduction",
                "source": "lender_portal",
                "recurring": True,
                "details": {"address": address},
                "priorYearData": {"mortgageInterest": prop.get("expenses", {}).get("mortgageInterest")},
            })
            next_id += 1

        docs.append({
            "id": next_id,
            "docType": "Property Tax Bill - RENTAL",
            "issuer": f"County Tax Assessor - {address}",
            "for": "Joint",
            "category": "Deduction",
            "source": "county_website",
            "recurring": True,
            "details": {"address": address},
            "priorYearData": {"taxes": prop.get("expenses", {}).get("taxes")},
        })
        next_id += 1

        if prop.get("expenses", {}).get("insurance"):
            docs.append({
                "id": next_id,
                "docType": "Property Insurance Declaration - RENTAL",
                "issuer": f"Insurance Provider - {address}",
                "for": "Joint",
                "category": "Deduction",
                "source": "insurance_portal",
                "recurring": True,
                "details": {"address": address},
                "priorYearData": {"premium": prop.get("expenses", {}).get("insurance")},
            })
            next_id += 1

    # K-1s from partnerships
    for partnership in income.get("partnerships", []):
        name = partnership["name"]
        if name.lower().strip() == "expenses":
            name = "Expenses Partnership"
        docs.append({
            "id": next_id,
            "docType": "K-1 (Form 1065)",
            "issuer": name,
            "ein": partnership.get("ein"),
            "for": "Primary",
            "category": "Income",
            "source": "partnership_portal_or_cpa",
            "recurring": True,
            "details": {
                "city": partnership.get("city", ""),
                "state": partnership.get("state", ""),
            },
            "priorYearData": {
                "netIncome": partnership.get("netIncome", partnership.get("rentalRealEstateLoss", 0)),
            },
        })
        next_id += 1

    # Deductions section
    deductions = profile.get("deductions", {})

    # Charitable contributions
    for contrib in deductions.get("charitableContributions", {}).get("cashContributions", []):
        docs.append({
            "id": next_id,
            "docType": "Charitable Contribution Receipt",
            "issuer": contrib["organization"],
            "for": "Joint",
            "category": "Deduction",
            "source": "user_records",
            "recurring": False,  # donations may not recur
            "priorYearData": {"amount": contrib.get("amount")},
        })
        next_id += 1

    # Primary residence mortgage (from Schedule A)
    mortgage = deductions.get("mortgageInterest", {})
    primary_address = profile.get("primaryResidence", {}).get("address", "")
    if mortgage.get("lender"):
        docs.append({
            "id": next_id,
            "docType": "Form 1098 (Mortgage Interest) - PRIMARY RESIDENCE",
            "issuer": mortgage["lender"],
            "for": "Joint",
            "category": "Deduction",
            "source": "lender_portal",
            "recurring": True,
            "details": {"address": primary_address},
            "priorYearData": {"totalReported1098": mortgage.get("totalReported1098")},
        })
        next_id += 1

    # HSA
    hsa = profile.get("hsa", {})
    if hsa:
        docs.append({
            "id": next_id,
            "docType": "Form 5498-SA / 1099-SA (HSA)",
            "issuer": "HSA Custodian",
            "for": hsa.get("beneficiary", "Unknown"),
            "category": "Other",
            "source": "hsa_provider_portal",
            "recurring": True,
            "priorYearData": {"coverageType": hsa.get("coverageType"), "employerContribution": hsa.get("employerContribution")},
        })
        next_id += 1

    # Prior-year return itself (carryovers)
    docs.append({
        "id": next_id,
        "docType": "Prior Year Federal Tax Return",
        "issuer": "CPA / Self",
        "for": "Joint",
        "category": "Other",
        "source": "cpa_provided",
        "recurring": True,
        "priorYearData": {"taxYear": profile.get("taxYear")},
    })
    next_id += 1

    # Forms filed — derive additional docs from uncommon forms
    forms_filed = profile.get("formsAndSchedulesFiled", [])
    if "Form 8889 (Health Savings Accounts)" in forms_filed and not hsa:
        docs.append({
            "id": next_id,
            "docType": "HSA Records",
            "issuer": "Unknown HSA Provider",
            "for": "Unknown",
            "category": "Other",
            "source": "hsa_provider_portal",
            "recurring": True,
        })
        next_id += 1

    if any("4868" in f or "Extension" in f for f in forms_filed):
        docs.append({
            "id": next_id,
            "docType": "Extension Filing Confirmation",
            "issuer": "IRS / CPA",
            "for": "Joint",
            "category": "Other",
            "source": "cpa_provided",
            "recurring": True,
        })
        next_id += 1

    if any("2210" in f or "Underpayment" in f for f in forms_filed):
        docs.append({
            "id": next_id,
            "docType": "Estimated Tax Payment Receipt",
            "issuer": "IRS / State",
            "for": "Joint",
            "category": "Other",
            "source": "bank_or_irs",
            "recurring": True,
        })
        next_id += 1

    return docs


CATEGORY_FOLDERS = {
    "W-2": "01 - W-2s & Employment",
    "Form 1095-C": "01 - W-2s & Employment",
    "1099-INT": "02 - Brokerage 1099s",
    "1099-DIV": "02 - Brokerage 1099s",
    "1099-B": "02 - Brokerage 1099s",
    "Consolidated 1099": "02 - Brokerage 1099s",
    "K-1 (Form 1065)": "03 - Partnerships & Rental Properties",
    "1099-MISC (Rental Income)": "03 - Partnerships & Rental Properties",
    "Form 1098 (Mortgage Interest) - RENTAL": "03 - Partnerships & Rental Properties",
    "Property Tax Bill - RENTAL": "03 - Partnerships & Rental Properties",
    "Property Insurance Declaration - RENTAL": "03 - Partnerships & Rental Properties",
    "Form 1098 (Mortgage Interest) - PRIMARY RESIDENCE": "04 - Primary Residence",
    "Property Tax Bill - PRIMARY RESIDENCE": "04 - Primary Residence",
    "Homestead Exemption Application/Approval": "04 - Primary Residence",
    "Homestead Exemption Approval": "04 - Primary Residence",
    "HUD-1 Closing Statement (Purchase)": "04 - Primary Residence",
    "HUD-1 Closing Statement (Sale)": "04 - Primary Residence",
    "Charitable Contribution Receipt": "05 - Charitable",
    "Form 5498-SA / 1099-SA (HSA)": "07 - HSA & Health Insurance [NEED DOCS]",
    "HSA Records": "07 - HSA & Health Insurance [NEED DOCS]",
    "Form 1095-C": "07 - HSA & Health Insurance [NEED DOCS]",
    "403b Records": "06 - Retirement Accounts",
    "401k Records": "06 - Retirement Accounts",
    "Schedule C Records": "08 - Business",
    "W-2 (Employee)": "08 - Business",
    "W-3 (Transmittal)": "08 - Business",
    "Form 941 (Quarterly)": "08 - Business",
    "Form 940 (FUTA)": "08 - Business",
    "Estimated Tax Payment Receipt": "09 - Tax Payments & Extensions [NEED DOCS]",
    "Extension Filing Confirmation": "09 - Tax Payments & Extensions [NEED DOCS]",
    "Prior Year Federal Tax Return": "10 - Carryovers & Prior Year [NEED FROM CPA]",
}

ISSUER_BRAND_MAP = {
    "charles schwab & co., inc": "Schwab",
    "charles schwab & co": "Schwab",
    "charles schwab": "Schwab",
    "schwab": "Schwab",
    "e*trade from morgan stanley": "E-Trade",
    "e*trade securities": "E-Trade",
    "e*trade": "E-Trade",
    "etrade": "E-Trade",
    "morgan stanley": "E-Trade - DoorDash Stock Plan",
    "robinhood markets inc.": "Robinhood",
    "robinhood markets inc": "Robinhood",
    "robinhood": "Robinhood",
    "wells fargo bank n.a.": "Wells Fargo",
    "wells fargo bank": "Wells Fargo",
    "wells fargo": "Wells Fargo",
    "fidelity investments": "Fidelity",
    "fidelity": "Fidelity",
    "chase": "Chase",
    "jpmorgan chase": "Chase",
    "doordash fka palo alto delivery": "DoorDash",
    "doordash": "DoorDash",
    "palo alto delivery": "DoorDash",
    "lucile salter packard childrens hospital": "Stanford Childrens",
    "texas childrens hospital": "Texas Childrens Hospital",
    "rpc 5402 south congress partners llc": "RPC 5402 South Congress LLC",
}


def _brand(issuer):
    """Normalize an issuer name to its short brand via ISSUER_BRAND_MAP."""
    key = issuer.lower().strip()
    key = re.sub(r"\s*-\s*\d+$", "", key)  # strip trailing account suffixes like "-1965"
    if key in ISSUER_BRAND_MAP:
        return ISSUER_BRAND_MAP[key]
    for map_key, brand in ISSUER_BRAND_MAP.items():
        if map_key in key or key in map_key:
            return brand
    return issuer.split("(")[0].strip()


US_STATE_CODES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY",
}
US_STATE_ABBREVS = set(US_STATE_CODES.values())

STREET_ABBREVIATIONS = {
    "lane": "Ln", "drive": "Dr", "street": "St", "avenue": "Ave",
    "boulevard": "Blvd", "road": "Rd", "court": "Ct", "circle": "Cir",
    "place": "Pl", "way": "Way", "trail": "Trl", "terrace": "Ter",
    "parkway": "Pkwy", "highway": "Hwy",
}


def _parse_address(address):
    """Parse an address into (street, city, state_code) components.

    Handles formats like:
      '211 Golden Eagle Lane, Brisbane, CA'
      '1414 Crown Forest Drive, Missouri City, Texas'
      '1414 Crown Forest Drive, Missouri City, TX 77459'
    """
    segments = [s.strip() for s in address.split(",")]

    street = segments[0] if segments else address
    city = ""
    state_code = ""

    for seg in segments[1:]:
        words = seg.split()
        last = words[-1] if words else ""

        if last.isdigit() and len(last) == 5:
            words = words[:-1]
            last = words[-1] if words else ""

        if last.upper() in US_STATE_ABBREVS:
            state_code = last.upper()
            city_words = words[:-1]
            if city_words:
                city = " ".join(city_words)
        elif last.lower() in US_STATE_CODES:
            state_code = US_STATE_CODES[last.lower()]
            city_words = words[:-1]
            if city_words:
                city = " ".join(city_words)
        elif " ".join(w.lower() for w in words) in US_STATE_CODES:
            state_code = US_STATE_CODES[" ".join(w.lower() for w in words)]
        elif not city:
            city = " ".join(words)

    return street, city, state_code


def _abbreviate_street(street):
    """Abbreviate common street type suffixes: Lane → Ln, Drive → Dr, etc."""
    words = street.split()
    if words:
        last_lower = words[-1].lower()
        if last_lower in STREET_ABBREVIATIONS:
            words[-1] = STREET_ABBREVIATIONS[last_lower]
    return " ".join(words)


def _short_address(address):
    """Produce a short address tag like '1414 Crown Forest Dr TX'."""
    street, city, state = _parse_address(address)
    short_street = _abbreviate_street(street)
    parts = [short_street]
    if state:
        parts.append(state)
    return " ".join(parts)


def _derive_subfolder(doc):
    """Derive the subfolder name within a category from document fields.

    Returns None if no subfolder is needed (doc goes directly in category folder).
    """
    dtype = doc.get("docType", "")
    issuer = doc.get("issuer", "")
    person = doc.get("for", "")
    details = doc.get("details", {})
    address = details.get("address", "")

    if dtype == "W-2":
        brand = _brand(issuer)
        if person and person not in ("Joint", "Unknown"):
            return f"{person} - {brand}"
        return brand

    if dtype in ("Consolidated 1099", "1099-INT", "1099-DIV", "1099-B"):
        return _brand(issuer)

    if "RENTAL" in dtype or dtype == "1099-MISC (Rental Income)":
        addr = address
        if not addr and " - " in issuer:
            addr = issuer.split(" - ", 1)[1]
        if addr:
            street, city, state = _parse_address(addr)
            short_street = _abbreviate_street(street)
            tag = short_street
            if state:
                tag += f" {state}"
            city_name = city or "Rental"
            return f"{city_name} Rental - {tag}"
        return _brand(issuer) if issuer else None

    if dtype == "K-1 (Form 1065)":
        city = details.get("city", "")
        state = details.get("state", "")
        brand = _brand(issuer)
        suffix = "" if doc.get("k1_status") == "received" else " [NEED K-1]"
        if city and state:
            return f"{city} {state} - {brand}{suffix}"
        return f"{brand}{suffix}"

    if dtype == "Schedule C Records":
        return None  # business name is embedded in category folder name

    if "Retirement" in dtype or "401k" in dtype or "403b" in dtype:
        return f"{_brand(issuer)} [NEED DOCS]"

    return None


def derive_folder_tree(docs, target_year):
    """Derive the full nested folder tree from documents.

    Each document gets a drivePath field set. Returns the full folder
    structure dict (path -> "") and modifies docs in place.
    """
    folders = {}

    for doc in docs:
        dtype = doc.get("docType", "")
        category = CATEGORY_FOLDERS.get(dtype, "00 - Uncategorized")

        # Primary residence and business embed details in the category name itself
        if dtype in ("Form 1098 (Mortgage Interest) - PRIMARY RESIDENCE",
                     "Property Tax Bill - PRIMARY RESIDENCE",
                     "Homestead Exemption Application/Approval",
                     "Homestead Exemption Approval",
                     "HUD-1 Closing Statement (Purchase)",
                     "HUD-1 Closing Statement (Sale)"):
            address = doc.get("details", {}).get("address", "")
            if address:
                category = f"04 - Primary Residence - {_short_address(address)}"

        if dtype == "Schedule C Records":
            biz_name = doc.get("issuer", "")
            if biz_name:
                category = f"08 - Business - {biz_name} [NEED DOCS]"

        if dtype in ("W-2 (Employee)", "W-3 (Transmittal)",
                     "Form 941 (Quarterly)", "Form 940 (FUTA)"):
            biz_name = doc.get("details", {}).get("business", "")
            if not biz_name:
                for other in docs:
                    if other.get("docType") == "Schedule C Records":
                        biz_name = other.get("issuer", "")
                        break
            if biz_name:
                category = f"08 - Business - {biz_name} [NEED DOCS]"

        subfolder = _derive_subfolder(doc)
        if subfolder:
            path = f"{category}/{subfolder}"
        else:
            path = category

        doc["drivePath"] = f"Taxes/{target_year}/{path}/"
        folders[path] = ""

        # Also ensure the parent category folder exists
        if subfolder:
            folders[category] = ""

    return dict(sorted(folders.items()))


def derive_folder_structure(docs, target_year):
    """Legacy wrapper — returns sorted list of top-level folders only."""
    tree = derive_folder_tree(docs, target_year)
    return sorted(set(p.split("/")[0] for p in tree.keys()))


import re


def _normalize_issuer(name):
    """Reduce an issuer name to fuzzy-matchable tokens.

    Strips EINs, account numbers, parenthetical suffixes, common legal
    suffixes, and punctuation so that 'Acme FKA Old Name Corp'
    and 'Acme Inc (EIN 12-3456789)' both reduce to 'acme'.
    """
    s = name.lower()
    s = re.sub(r"\(.*?\)", "", s)                   # (EIN ...), (Box A), etc.
    s = re.sub(r"\bein\s*[\d-]+", "", s)
    s = re.sub(r"\bacct?\s*#?\s*[\w-]+", "", s)     # acct -XXXX, account YYYY
    s = re.sub(r"\b(inc|llc|llp|lp|corp|co|n\.?a\.?|fka|dba)\b", "", s)
    s = re.sub(r"[.,&*'\"-]", " ", s)
    return " ".join(s.split())                       # collapse whitespace


def _normalize_doctype(dtype):
    """Reduce a docType to a canonical form for matching."""
    s = dtype.lower()
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"\s*-\s*(rental|primary residence)\b", "", s)
    s = re.sub(r"\s*-\s*$", "", s)
    s = " ".join(s.split())
    TYPE_ALIASES = {
        "consolidated 1099": "1099",
        "form 1098": "1098",
        "form 1098 mortgage interest": "1098",
        "property tax bill": "property tax",
        "tx property tax bill": "property tax",
        "property insurance declaration": "insurance",
        "property insurance declaration rental": "insurance",
        "schedule c records": "schedule c",
        "schedule c records": "schedule c",
        "prior year carryover summary": "prior year return",
        "prior year federal tax return": "prior year return",
        "form 5498-sa / 1099-sa hsa": "hsa",
        "hsa records": "hsa",
        "1099-misc": "1099-misc rental",
        "form 1099-misc": "1099-misc rental",
        "brokerage account tax form": "1099",
    }
    return TYPE_ALIASES.get(s, s)


GENERIC_ISSUER_PREFIXES = [
    "property manager", "county tax assessor", "insurance provider",
    "hsa custodian", "cpa", "self", "unknown",
]


def _is_generic_issuer(name):
    return any(name.startswith(p) for p in GENERIC_ISSUER_PREFIXES)


def _fuzzy_issuer_match(a, b):
    """Check if two normalized issuers refer to the same entity."""
    if a == b:
        return True
    if _is_generic_issuer(a) or _is_generic_issuer(b):
        return True
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a or not tokens_b:
        return False
    overlap = tokens_a & tokens_b
    smaller = min(len(tokens_a), len(tokens_b))
    return len(overlap) >= max(1, smaller * 0.5)


def diff_against_registry(derived_docs, registry):
    """Compare derived docs against an existing registry and report coverage."""
    registry_docs = registry.get("documents", [])

    derived_items = [
        (_normalize_doctype(d.get("docType", "")), _normalize_issuer(d.get("issuer", "")), d)
        for d in derived_docs
    ]
    registry_items = [
        (_normalize_doctype(d.get("docType", "")), _normalize_issuer(d.get("issuer", "")), d)
        for d in registry_docs
    ]

    matched = []
    derived_only = []
    registry_matched_idx = set()

    for d_dtype, d_issuer, d_doc in derived_items:
        found = False
        for i, (r_dtype, r_issuer, r_doc) in enumerate(registry_items):
            if i in registry_matched_idx:
                continue
            if d_dtype == r_dtype and _fuzzy_issuer_match(d_issuer, r_issuer):
                matched.append((d_doc, r_doc))
                registry_matched_idx.add(i)
                found = True
                break
        if not found:
            derived_only.append(d_doc)

    registry_only = [
        r_doc for i, (_, _, r_doc) in enumerate(registry_items)
        if i not in registry_matched_idx
    ]

    total_registry = len(registry_items)
    match_pct = (len(matched) / total_registry * 100) if total_registry else 0

    return {
        "summary": {
            "derivedCount": len(derived_items),
            "registryCount": total_registry,
            "matchedCount": len(matched),
            "matchPercent": round(match_pct, 1),
            "derivedOnlyCount": len(derived_only),
            "registryOnlyCount": len(registry_only),
        },
        "matched": sorted([
            f"{d.get('docType')} [{d.get('issuer')}] <-> {r.get('docType')} [{r.get('issuer')}]"
            for d, r in matched
        ]),
        "derivedOnly": sorted([f"{d.get('docType')} | {d.get('issuer')}" for d in derived_only]),
        "registryOnly": sorted([f"{d.get('docType')} | {d.get('issuer')}" for d in registry_only]),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Derive a document registry from a prior-year return profile.",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Path to prior-year profile JSON (default: knowledge-base/profile-{year}.json)",
    )
    parser.add_argument(
        "--target-year",
        type=int,
        default=None,
        help="Target tax year (default: profile year + 1)",
    )
    parser.add_argument(
        "--diff",
        default=None,
        help="Path to existing registry JSON to diff against",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="Write derived registry to this path",
    )
    args = parser.parse_args()

    profile_path = args.profile
    if not profile_path:
        for year in range(2024, 2020, -1):
            p = kb_path(f"profile-{year}.json")
            if os.path.exists(p):
                profile_path = p
                break
    if not profile_path or not os.path.exists(profile_path):
        print("Error: no prior-year profile found.", file=sys.stderr)
        sys.exit(1)

    profile = load_json(profile_path)
    profile_year = profile.get("taxYear", 2024)
    target_year = args.target_year or (profile_year + 1)

    print(f"Prior-year profile: {profile_year} ({os.path.basename(profile_path)})")
    print(f"Deriving registry for: {target_year}\n")

    docs = derive_documents(profile, target_year)
    folder_tree = derive_folder_tree(docs, target_year)
    top_folders = sorted(set(p.split("/")[0] for p in folder_tree.keys()))

    print(f"Derived {len(docs)} expected documents:")
    for d in docs:
        recur = "recurring" if d.get("recurring") else "may not recur"
        path = d.get("drivePath", "?")
        print(f"  [{d['id']:2d}] {d['docType']:45s} | {d['issuer']:40s} | {d['for']:8s} | {path}")

    print(f"\nDerived {len(top_folders)} top-level folders, {len(folder_tree)} total paths:")
    for p in sorted(folder_tree.keys()):
        depth = p.count("/")
        indent = "  " * (depth + 1)
        label = p.split("/")[-1] if "/" in p else p
        print(f"{indent}{label}/")

    if args.diff:
        registry = load_json(args.diff)
        report = diff_against_registry(docs, registry)
        s = report["summary"]
        print(f"\n{'='*60}")
        print(f"DIFF: Derived ({s['derivedCount']}) vs Registry ({s['registryCount']})")
        print(f"Matched: {s['matchedCount']} ({s['matchPercent']}%)")
        print(f"Derived only (not in registry): {s['derivedOnlyCount']}")
        print(f"Registry only (not derived): {s['registryOnlyCount']}")

        if report["derivedOnly"]:
            print(f"\nDerived but NOT in registry (parser found, registry missing):")
            for item in report["derivedOnly"]:
                print(f"  + {item}")

        if report["registryOnly"]:
            print(f"\nIn registry but NOT derived (user input or new-year events):")
            for item in report["registryOnly"]:
                print(f"  - {item}")

        if args.json_out:
            out = os.path.expanduser(args.json_out)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "w") as f:
                json.dump({"derived_docs": docs, "diff_report": report}, f, indent=2)
            print(f"\nSaved to {out}")
    elif args.json_out:
        out = os.path.expanduser(args.json_out)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        registry_out = {
            "taxYear": target_year,
            "documents": docs,
            "driveFolderStructure": folder_tree,
        }
        with open(out, "w") as f:
            json.dump(registry_out, f, indent=2)
        print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
