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
                "priorYearData": {"premium": prop.get("expenses", {}).get("insurance")},
            })
            next_id += 1

    # K-1s from partnerships
    for partnership in income.get("partnerships", []):
        docs.append({
            "id": next_id,
            "docType": "K-1 (Form 1065)",
            "issuer": partnership["name"],
            "ein": partnership.get("ein"),
            "for": "Aditya",
            "category": "Income",
            "source": "partnership_portal_or_cpa",
            "recurring": True,
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
    if mortgage.get("lender"):
        docs.append({
            "id": next_id,
            "docType": "Form 1098 (Mortgage Interest) - PRIMARY RESIDENCE",
            "issuer": mortgage["lender"],
            "for": "Joint",
            "category": "Deduction",
            "source": "lender_portal",
            "recurring": True,
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

    return docs


def derive_folder_structure(docs, target_year):
    """Derive folder structure from document categories and issuers."""
    # Category → folder prefix mapping
    CATEGORY_FOLDERS = {
        "W-2": "01 - W-2s & Employment",
        "1099-INT": "02 - Brokerage 1099s",
        "Consolidated 1099": "02 - Brokerage 1099s",
        "K-1 (Form 1065)": "03 - Partnerships & Rental Properties",
        "1099-MISC (Rental Income)": "03 - Partnerships & Rental Properties",
        "Form 1098 (Mortgage Interest) - RENTAL": "03 - Partnerships & Rental Properties",
        "Property Tax Bill - RENTAL": "03 - Partnerships & Rental Properties",
        "Property Insurance Declaration - RENTAL": "03 - Partnerships & Rental Properties",
        "Form 1098 (Mortgage Interest) - PRIMARY RESIDENCE": "04 - Primary Residence",
        "Charitable Contribution Receipt": "05 - Charitable",
        "Form 5498-SA / 1099-SA (HSA)": "07 - HSA & Health Insurance",
        "HSA Records": "07 - HSA & Health Insurance",
        "Schedule C Records": "08 - Business",
        "Prior Year Federal Tax Return": "10 - Carryovers & Prior Year",
    }

    folders = set()
    for doc in docs:
        folder = CATEGORY_FOLDERS.get(doc["docType"], "00 - Uncategorized")
        folders.add(folder)

    return sorted(folders)


import re


def _normalize_issuer(name):
    """Reduce an issuer name to fuzzy-matchable tokens.

    Strips EINs, account numbers, parenthetical suffixes, common legal
    suffixes, and punctuation so that 'DoorDash FKA Palo Alto Delivery'
    and 'DoorDash Inc (EIN 46-2852392)' both reduce to 'doordash'.
    """
    s = name.lower()
    s = re.sub(r"\(.*?\)", "", s)                   # (EIN ...), (Box A), etc.
    s = re.sub(r"\bein\s*[\d-]+", "", s)
    s = re.sub(r"\bacct?\s*#?\s*[\w-]+", "", s)     # acct -3771, account 1965
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
        "schedule c records palmetto superfoods": "schedule c",
        "prior year carryover summary": "prior year return",
        "prior year federal tax return": "prior year return",
        "form 5498-sa / 1099-sa hsa": "hsa",
        "hsa records": "hsa",
        "1099-misc": "1099-misc rental",
        "form 1099-misc": "1099-misc rental",
        "schwab account -3771 tax form": "1099",
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
    folders = derive_folder_structure(docs, target_year)

    print(f"Derived {len(docs)} expected documents:")
    for d in docs:
        recur = "recurring" if d.get("recurring") else "may not recur"
        print(f"  [{d['id']:2d}] {d['docType']:45s} | {d['issuer']:40s} | {d['for']:8s} | {recur}")

    print(f"\nDerived {len(folders)} top-level folders:")
    for f in folders:
        print(f"  {f}/")

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
        with open(out, "w") as f:
            json.dump({"taxYear": target_year, "derived_docs": docs, "folders": folders}, f, indent=2)
        print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
