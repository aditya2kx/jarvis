#!/usr/bin/env python3
"""Onboard a new user from their prior-year tax return PDF.

This is CHITRA's primary bootstrap capability. Given a tax return PDF
(local file or Google Drive file ID), this script:

  1. Extracts text from the PDF using pdfplumber
  2. Outputs the text + parsing instructions for CHITRA (the AI agent)
     to produce a structured profile JSON
  3. Once the profile exists, derives the document registry for the next year
  4. Generates a friendly questionnaire for the user

This is designed to work for ANY user — not just a specific taxpayer.

Usage:
    # From a local PDF:
    python onboard_from_return.py --pdf /path/to/2024-federal-return.pdf

    # From Google Drive:
    python onboard_from_return.py --drive-id 1Omn7-7VWUz7PrA-McjEQ56aZcBDwUTBl

    # If profile already exists, skip to registry + questionnaire:
    python onboard_from_return.py --profile agents/chitra/knowledge-base/profile-2024.json
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
from core.config_loader import project_dir, kb_path

SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "knowledge-base", "schema", "return-profile.schema.md"
)


def step1_extract_pdf(pdf_path=None, drive_id=None):
    """Extract text from a tax return PDF. Returns (text, txt_path)."""
    extracted_dir = os.path.join(project_dir(), "extracted")
    os.makedirs(extracted_dir, exist_ok=True)

    if drive_id and not pdf_path:
        from skills.pdf.extract import download_pdf
        from core.config_loader import refresh_access_token
        token = refresh_access_token()
        pdf_path = os.path.join(extracted_dir, f"return-{drive_id[:8]}.pdf")
        download_pdf(drive_id, pdf_path, token)

    if not pdf_path or not os.path.exists(pdf_path):
        print(f"Error: PDF not found at {pdf_path}", file=sys.stderr)
        sys.exit(1)

    from skills.pdf.extract import extract_text
    txt_path = os.path.splitext(pdf_path)[0] + ".txt"
    text = extract_text(pdf_path, txt_path)
    return text, txt_path


def step2_print_parsing_prompt(txt_path):
    """Print the prompt that CHITRA should use to parse the return text."""
    schema_text = ""
    if os.path.exists(SCHEMA_PATH):
        with open(SCHEMA_PATH) as f:
            schema_text = f.read()

    print("\n" + "=" * 70)
    print("STEP 2: CHITRA PARSING INSTRUCTIONS")
    print("=" * 70)
    print()
    print("The extracted return text has been saved to:")
    print(f"  {txt_path}")
    print()
    print("CHITRA: Please read the extracted text and produce a structured")
    print("profile JSON following this schema. Extract EVERY issuer, entity,")
    print("amount, and form. Do not skip any schedule or attachment.")
    print()
    print("Save the result as:")
    print(f"  agents/chitra/knowledge-base/profile-YYYY.json")
    print("  (where YYYY is the tax year from the return)")
    print()
    if schema_text:
        print("-" * 70)
        print("SCHEMA REFERENCE:")
        print("-" * 70)
        print(schema_text[:3000])
        if len(schema_text) > 3000:
            print(f"\n... (full schema at {SCHEMA_PATH})")
    print()
    print("KEY PARSING PRIORITIES:")
    print("  1. Form 1040 → filing status, total income, AGI, taxable income")
    print("  2. Schedule B → every interest/dividend payer with amounts")
    print("  3. Schedule C → business name, gross receipts, expenses")
    print("  4. Schedule D + 8949 → capital gains by broker, short vs long")
    print("  5. Schedule E p1 → rental properties with addresses, lenders")
    print("  6. Schedule E p2 → partnerships/S-corps with names and EINs")
    print("  7. Schedule A → SALT, mortgage interest (with lender), charitable")
    print("  8. Form 8889 → HSA details")
    print("  9. Form 8582 → passive activity losses and carryovers")
    print("  10. K-1 attachments → every entity name and EIN")
    print("  11. State returns → which states, key figures")
    print()
    print("Record the EXACT LEGAL NAME of each issuer/entity — this is what")
    print("will appear on next year's documents.")


def step3_derive_registry(profile_path, target_year=None):
    """Derive registry and questionnaire from a profile."""
    from agents.chitra.scripts.derive_registry_from_return import (
        load_json, derive_documents, derive_folder_structure,
    )
    from agents.chitra.scripts.generate_questionnaire import (
        load_json as load_q,
        generate_confirmation_questions,
        generate_discovery_questions,
        format_questionnaire,
    )

    profile = load_json(profile_path)
    prior_year = profile.get("taxYear", 2024)
    year = target_year or (prior_year + 1)

    docs = derive_documents(profile, year)
    folders = derive_folder_structure(docs, year)

    print(f"\n{'=' * 70}")
    print(f"STEP 3: DERIVED REGISTRY FOR {year}")
    print(f"{'=' * 70}")
    print(f"\n  Documents expected: {len(docs)}")
    print(f"  Folder categories:  {len(folders)}")
    for d in docs:
        print(f"    [{d['id']:2d}] {d['docType']:40s} | {d['issuer']}")

    registry_out = kb_path(f"derived-registry-{year}.json")
    os.makedirs(os.path.dirname(registry_out), exist_ok=True)
    with open(registry_out, "w") as f:
        json.dump({
            "taxYear": year,
            "derivedFrom": f"profile-{prior_year}.json",
            "documents": docs,
            "driveFolderStructure": {folder: "" for folder in folders},
        }, f, indent=2)
    print(f"\n  Registry saved: {registry_out}")

    confirmation = generate_confirmation_questions(profile, year)
    discovery = generate_discovery_questions(profile, year)
    md = format_questionnaire(confirmation, discovery, year)

    q_out = kb_path(f"questionnaire-{year}.md")
    with open(q_out, "w") as f:
        f.write(md)
    print(f"  Questionnaire saved: {q_out}")
    print(f"  Total questions: {len(confirmation) + len(discovery)} "
          f"({len(confirmation)} confirmation + {len(discovery)} discovery)")

    return docs, folders, md


def main():
    parser = argparse.ArgumentParser(
        description="Onboard a new user from their tax return PDF.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pdf", help="Path to local tax return PDF")
    group.add_argument("--drive-id", help="Google Drive file ID of the tax return PDF")
    group.add_argument("--profile", help="Skip PDF extraction — use existing profile JSON")
    parser.add_argument(
        "--target-year", type=int, default=None,
        help="Target tax year (default: profile year + 1)",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("CHITRA — New User Onboarding from Tax Return")
    print("=" * 70)

    if args.profile:
        if not os.path.exists(args.profile):
            print(f"Error: profile not found at {args.profile}", file=sys.stderr)
            sys.exit(1)
        print(f"\nUsing existing profile: {args.profile}")
        print("(Skipping PDF extraction — going straight to registry + questionnaire)")
        step3_derive_registry(args.profile, args.target_year)
    else:
        print("\nSTEP 1: Extracting text from tax return PDF...")
        text, txt_path = step1_extract_pdf(
            pdf_path=args.pdf,
            drive_id=args.drive_id,
        )
        print(f"  Extracted {len(text):,} characters from PDF")

        step2_print_parsing_prompt(txt_path)

        print("\n" + "=" * 70)
        print("NEXT: After CHITRA produces the profile JSON, run this again with:")
        print(f"  python {__file__} --profile <path-to-profile.json>")
        print("=" * 70)


if __name__ == "__main__":
    main()
