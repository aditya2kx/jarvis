#!/usr/bin/env python3
"""Generate a friendly onboarding questionnaire for a new tax year.

Takes a prior-year profile JSON and produces categorized, plain-English
questions that any non-tax-professional can answer. The answers let CHITRA
fill in everything the prior-year return can't predict.

The questions fall into two buckets:
  1. CONFIRMATION — things the return DID show that might have changed
  2. DISCOVERY — life events the return CAN'T predict

Usage:
    python generate_questionnaire.py [--profile path/to/profile.json] [--target-year 2025]
    python generate_questionnaire.py --json-out questionnaire.json
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
from core.config_loader import kb_path


def load_json(path):
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Question templates by category
# ---------------------------------------------------------------------------

def generate_confirmation_questions(profile, target_year):
    """Questions that confirm whether prior-year items still apply."""
    questions = []
    income = profile.get("income", {})
    prior_year = profile.get("taxYear", target_year - 1)

    # --- Jobs & Employment ---
    employers = income.get("wages", {}).get("employers", [])
    for emp in employers:
        name = emp["name"]
        person = emp.get("for", "you")
        questions.append({
            "category": "Jobs & Employment",
            "icon": "💼",
            "question": f"Is {person} still working at {name}?",
            "why": f"In {prior_year}, {person} received a W-2 from {name}. "
                   f"If they left or changed employers, we need the new W-2 "
                   f"and possibly a final paycheck stub from {name}.",
            "ifNo": f"We'll need the W-2 from the new employer and the final "
                    f"W-2 from {name} (both may issue one).",
            "docTypes": ["W-2"],
        })

    # --- Brokerage Accounts ---
    brokers_raw = set()
    for period in ["shortTerm", "longTerm"]:
        for txn in income.get("capitalGains", {}).get(period, {}).get("transactions", []):
            broker = txn["description"].split("(")[0].strip()
            brokers_raw.add(broker)
    for src in income.get("interest", {}).get("sources", []):
        if "K-1" not in src.get("payer", ""):
            brokers_raw.add(src["payer"])

    # Deduplicate: strip account numbers, trailing punctuation, and
    # consolidate variants like "Charles Schwab & Co., Inc -1965" into one
    seen_normalized = {}
    for b in brokers_raw:
        norm = re.sub(r"\s*-\s*\d+$", "", b).rstrip(".")
        if norm not in seen_normalized:
            seen_normalized[norm] = b
    brokers = set(seen_normalized.values())

    for broker in sorted(brokers):
        questions.append({
            "category": "Investments & Brokerage",
            "icon": "📈",
            "question": f"Do you still have an account at {broker}?",
            "why": f"In {prior_year}, we saw trading or interest income from {broker}. "
                   f"If the account is closed or transferred, we may need a final 1099.",
            "ifNo": "Let us know where the account was transferred to — we'll need the "
                    "1099 from the new broker.",
            "docTypes": ["Consolidated 1099", "1099-INT", "1099-B"],
        })

    # --- Rental Properties ---
    for prop in income.get("rentalRealEstate", {}).get("properties", []):
        addr = prop.get("address", "your rental property")
        questions.append({
            "category": "Rental Properties",
            "icon": "🏠",
            "question": f"Do you still own and rent out {addr}?",
            "why": f"In {prior_year}, this property generated ${prop.get('rentReceived', 0):,.0f} "
                   f"in rental income. If you sold it or stopped renting, that changes the "
                   f"tax picture significantly.",
            "ifNo": "If sold: we need the closing statement (HUD-1) for capital gains. "
                    "If converted to personal use: we stop Schedule E reporting.",
            "docTypes": ["1099-MISC", "Form 1098", "Property Tax Bill", "Insurance"],
        })
        if prop.get("mortgageLender"):
            questions.append({
                "category": "Rental Properties",
                "icon": "🏠",
                "question": f"Is your mortgage on {addr} still with {prop['mortgageLender']}?",
                "why": "If you refinanced, the new lender issues the 1098. "
                       "We need to know who to expect it from.",
                "ifNo": "Tell us the new lender — we'll watch for their 1098.",
                "docTypes": ["Form 1098"],
            })
        questions.append({
            "category": "Rental Properties",
            "icon": "🏠",
            "question": f"Who manages {addr}? (property manager name or 'self-managed')",
            "why": "The property manager issues a 1099-MISC for your rental income. "
                   "We need to know who to expect it from.",
            "freeText": True,
            "docTypes": ["1099-MISC"],
        })
        questions.append({
            "category": "Rental Properties",
            "icon": "🏠",
            "question": f"Who provides insurance for {addr}?",
            "why": "Landlord insurance is a deductible expense on Schedule E. "
                   "We need the declaration page showing the annual premium.",
            "freeText": True,
            "docTypes": ["Insurance Declaration"],
        })

    # --- Partnerships ---
    for p in income.get("partnerships", []):
        questions.append({
            "category": "Partnerships & Investments",
            "icon": "🤝",
            "question": f"Are you still invested in {p['name']}?",
            "why": f"In {prior_year}, you received a K-1 from {p['name']}. "
                   f"If you sold your interest or the entity dissolved, that's a "
                   f"different tax event.",
            "ifNo": "If sold: we need the final K-1 plus capital gain/loss calculation. "
                    "If dissolved: the final K-1 will show the wind-down.",
            "docTypes": ["K-1"],
        })

    # --- Business (Schedule C) ---
    biz = income.get("businessIncome", {})
    if biz.get("businessName"):
        questions.append({
            "category": "Your Business",
            "icon": "🏪",
            "question": f"Is {biz['businessName']} still operating?",
            "why": f"In {prior_year}, {biz['businessName']} was reported on Schedule C. "
                   f"If it's closed or converted to an LLC/S-Corp, the filing changes.",
            "ifNo": "If closed: we report the final Schedule C. "
                    "If converted: we need the new entity's tax docs.",
            "docTypes": ["Schedule C Records"],
        })
        questions.append({
            "category": "Your Business",
            "icon": "🏪",
            "question": f"Does {biz['businessName']} have any employees?",
            "why": "If yes, you'll have employer tax obligations: W-2s for employees, "
                   "W-3 transmittal, quarterly 941s, and annual 940 (FUTA).",
            "ifYes": "We'll need: employee W-2s, W-3, Form 941 (each quarter), Form 940.",
            "docTypes": ["W-2", "W-3", "Form 941", "Form 940"],
        })

    # --- Charitable ---
    for contrib in profile.get("deductions", {}).get("charitableContributions", {}).get("cashContributions", []):
        questions.append({
            "category": "Charitable Giving",
            "icon": "🎁",
            "question": f"Did you make any charitable donations in {target_year}? "
                        f"(In {prior_year}, you donated ${contrib.get('amount', 0):,.0f} "
                        f"to {contrib['organization']})",
            "why": "Charitable donations are deductible if you itemize. "
                   "We need receipts or acknowledgment letters for any donations over $250.",
            "freeText": True,
            "docTypes": ["Charitable Contribution Receipt"],
        })

    # --- HSA ---
    hsa = profile.get("hsa", {})
    if hsa:
        questions.append({
            "category": "Health & Insurance",
            "icon": "🏥",
            "question": f"Do you still have a Health Savings Account (HSA)? "
                        f"(In {prior_year}, {hsa.get('beneficiary', 'you')} had "
                        f"{hsa.get('coverageType', 'unknown')} coverage)",
            "why": "HSA contributions are tax-deductible and distributions for medical "
                   "expenses are tax-free. We need Forms 5498-SA and 1099-SA from your "
                   "HSA provider.",
            "ifNo": "If you closed the HSA or switched to non-HDHP insurance, "
                    "we need the final 1099-SA.",
            "docTypes": ["Form 5498-SA", "Form 1099-SA"],
        })

    # --- Mortgage (primary residence from deductions) ---
    mortgage = profile.get("deductions", {}).get("mortgageInterest", {})
    if mortgage.get("lender"):
        questions.append({
            "category": "Your Home",
            "icon": "🏡",
            "question": f"Is your home mortgage still with {mortgage['lender']}?",
            "why": "The mortgage lender sends a Form 1098 showing interest paid, "
                   "which is deductible on Schedule A.",
            "ifNo": "Tell us the new lender. If you refinanced, both the old and "
                    "new lender may issue a 1098.",
            "docTypes": ["Form 1098"],
        })

    return questions


def generate_discovery_questions(profile, target_year):
    """Life-event questions the prior-year return can't predict."""
    prior_year = profile.get("taxYear", target_year - 1)
    questions = []

    questions.append({
        "category": "Major Life Events",
        "icon": "🌟",
        "question": f"Did you buy, sell, or refinance any real estate in {target_year}?",
        "why": "Buying a home means new mortgage interest deductions. "
               "Selling means capital gains or losses to report. "
               "Refinancing may change lenders on the 1098.",
        "examples": "New primary residence, investment property, vacation home, "
                    "or selling any of the above.",
        "docTypes": ["Form 1098", "HUD-1 Closing Statement", "Property Tax Bill"],
    })

    questions.append({
        "category": "Major Life Events",
        "icon": "🌟",
        "question": f"Did you move to a different state in {target_year}?",
        "why": "Moving states affects which state returns you file, "
               "how income is allocated, and may trigger partial-year filings.",
        "examples": "Moved from California to Texas, relocated for work, etc.",
        "docTypes": ["State W-2s", "Moving expenses documentation"],
    })

    questions.append({
        "category": "Major Life Events",
        "icon": "🌟",
        "question": f"Did you get married, divorced, or have a child in {target_year}?",
        "why": "These change your filing status and may add dependents, "
               "which affects your tax bracket and available credits.",
        "docTypes": [],
    })

    questions.append({
        "category": "Investments & Stock",
        "icon": "📈",
        "question": f"Did you exercise any stock options (ISOs or NSOs) in {target_year}?",
        "why": "ISO exercises can trigger Alternative Minimum Tax (AMT). "
               "NSO exercises create ordinary income. Both need Form 3921 "
               "from your employer.",
        "examples": "Exercised 1,000 shares of company ISOs, bought shares through ESPP, etc.",
        "docTypes": ["Form 3921", "Form 3922"],
    })

    questions.append({
        "category": "Investments & Stock",
        "icon": "📈",
        "question": f"Did you buy or sell any cryptocurrency in {target_year}?",
        "why": "Starting 2025, brokers issue Form 1099-DA for crypto. "
               "Any sale, swap, or conversion is a taxable event.",
        "docTypes": ["Form 1099-DA", "1099-B"],
    })

    questions.append({
        "category": "Investments & Stock",
        "icon": "📈",
        "question": f"Did you open any new brokerage or investment accounts in {target_year}?",
        "why": "New accounts may generate 1099s we don't know about. "
               "Better to know upfront than chase them at filing time.",
        "freeText": True,
        "docTypes": ["Consolidated 1099"],
    })

    questions.append({
        "category": "Retirement & Savings",
        "icon": "🏦",
        "question": f"Did you contribute to or withdraw from any retirement accounts? "
                    f"(401k, IRA, Roth, pension)",
        "why": "Contributions may be deductible; withdrawals are usually taxable. "
               "We need Forms 1099-R (distributions) and 5498 (contributions) "
               "from your plan providers.",
        "docTypes": ["Form 1099-R", "Form 5498"],
    })

    questions.append({
        "category": "Retirement & Savings",
        "icon": "🏦",
        "question": "Does your spouse have a retirement plan at work? (401k, 403b, pension, etc.)",
        "why": "Employer retirement plans generate annual statements. "
               "Even if there were no distributions, contributions are relevant for "
               "deduction eligibility.",
        "freeText": True,
        "docTypes": ["Retirement Account Statement"],
    })

    questions.append({
        "category": "Insurance & Health",
        "icon": "🏥",
        "question": "Did you have health insurance through your employer all year?",
        "why": "Your employer will send Form 1095-C confirming coverage. "
               "If you had a gap or bought marketplace insurance, "
               "there may be a Form 1095-A instead.",
        "docTypes": ["Form 1095-C", "Form 1095-A"],
    })

    questions.append({
        "category": "Insurance & Health",
        "icon": "🏥",
        "question": "Do you have any life insurance policies with cash value? (IUL, whole life, etc.)",
        "why": "Usually no tax impact unless you took withdrawals, loans that lapsed, "
               "or surrendered the policy. If so, you'll get a 1099.",
        "docTypes": ["1099-R", "Annual Statement"],
    })

    questions.append({
        "category": "Your Business",
        "icon": "🏪",
        "question": f"Did you start any new businesses or side income in {target_year}?",
        "why": "Any self-employment income over $400 needs a Schedule C. "
               "This includes freelancing, consulting, gig work, or a new LLC.",
        "freeText": True,
        "docTypes": ["1099-NEC", "Schedule C Records"],
    })

    questions.append({
        "category": "Your Business",
        "icon": "🏪",
        "question": f"Did you invest in any new partnerships or real estate deals in {target_year}?",
        "why": "New partnerships will send K-1s (often late — March to September). "
               "We need to know about them early so we can plan for extensions.",
        "freeText": True,
        "docTypes": ["K-1"],
    })

    questions.append({
        "category": "Education & Student Loans",
        "icon": "🎓",
        "question": f"Did you or your spouse pay student loan interest in {target_year}?",
        "why": "Student loan interest may be deductible (Form 1098-E), though "
               "it phases out at higher incomes. Your loan servicer sends the form.",
        "docTypes": ["Form 1098-E"],
    })

    questions.append({
        "category": "Taxes & Payments",
        "icon": "💰",
        "question": f"Did you make any estimated tax payments or extension payments for {target_year}?",
        "why": "Estimated payments (federal Form 1040-ES or state equivalents) and "
               "extension payments reduce your balance due. We need dates and amounts.",
        "freeText": True,
        "docTypes": ["Form 1040-ES", "Extension payment receipts"],
    })

    questions.append({
        "category": "Taxes & Payments",
        "icon": "💰",
        "question": "Who is preparing your taxes this year? (CPA name/firm, self, TurboTax, etc.)",
        "why": "If you switched CPAs, we need to transfer prior-year data. "
               "If self-preparing, we'll need all the source documents directly.",
        "freeText": True,
        "docTypes": [],
    })

    questions.append({
        "category": "Your Home",
        "icon": "🏡",
        "question": f"Did you file for a homestead exemption on your primary residence in {target_year}?",
        "why": "A homestead exemption reduces your property tax, which affects "
               "the SALT deduction. We'll need the approval letter from your county.",
        "docTypes": ["Homestead Exemption Approval"],
    })

    return questions


def format_questionnaire(confirmation, discovery, target_year):
    """Format all questions into a readable output."""
    all_qs = confirmation + discovery
    categories = {}
    for q in all_qs:
        cat = q["category"]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(q)

    lines = [
        f"# {target_year} Tax Year Onboarding Questionnaire",
        f"",
        f"Hi! I'm CHITRA, your tax document assistant. I've reviewed your "
        f"{target_year - 1} tax return and have a good picture of your "
        f"financial situation. To make sure I collect everything for "
        f"{target_year}, I have some questions grouped by topic.",
        f"",
        f"**No tax expertise needed** — just answer honestly and I'll handle "
        f"the rest. If you're unsure about something, just say so and I'll "
        f"explain further.",
        f"",
        f"---",
        f"",
    ]

    q_num = 0
    for cat, qs in categories.items():
        icon = qs[0].get("icon", "📋")
        lines.append(f"## {icon} {cat}")
        lines.append("")
        for q in qs:
            q_num += 1
            lines.append(f"**Q{q_num}.** {q['question']}")
            lines.append(f"")
            lines.append(f"  *Why I'm asking:* {q['why']}")
            if q.get("examples"):
                lines.append(f"  *Examples:* {q['examples']}")
            if q.get("ifNo"):
                lines.append(f"  *If no:* {q['ifNo']}")
            if q.get("ifYes"):
                lines.append(f"  *If yes:* {q['ifYes']}")
            lines.append("")
        lines.append("---")
        lines.append("")

    lines.append(f"That's it! Once you answer these, I'll know exactly which "
                 f"documents to collect for {target_year} and can start tracking "
                 f"them down automatically.")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Generate a tax year onboarding questionnaire from a prior-year profile.",
    )
    parser.add_argument(
        "--profile", default=None,
        help="Path to prior-year profile JSON (default: auto-detect)",
    )
    parser.add_argument(
        "--target-year", type=int, default=None,
        help="Target tax year (default: profile year + 1)",
    )
    parser.add_argument(
        "--json-out", default=None,
        help="Write structured questionnaire JSON to this path",
    )
    parser.add_argument(
        "--markdown-out", default=None,
        help="Write formatted markdown questionnaire to this path",
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
    prior_year = profile.get("taxYear", 2024)
    target_year = args.target_year or (prior_year + 1)

    print(f"Generating questionnaire for {target_year} "
          f"(based on {prior_year} return)\n")

    confirmation = generate_confirmation_questions(profile, target_year)
    discovery = generate_discovery_questions(profile, target_year)

    print(f"  Confirmation questions (from return): {len(confirmation)}")
    print(f"  Discovery questions (life events):    {len(discovery)}")
    print(f"  Total questions:                      {len(confirmation) + len(discovery)}")

    md = format_questionnaire(confirmation, discovery, target_year)

    if args.markdown_out:
        out = os.path.expanduser(args.markdown_out)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w") as f:
            f.write(md)
        print(f"\nMarkdown saved to {out}")
    else:
        print(f"\n{'=' * 60}")
        print(md)

    if args.json_out:
        out = os.path.expanduser(args.json_out)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w") as f:
            json.dump({
                "taxYear": target_year,
                "basedOnReturn": prior_year,
                "confirmation": confirmation,
                "discovery": discovery,
            }, f, indent=2)
        print(f"JSON saved to {out}")


if __name__ == "__main__":
    main()
