#!/usr/bin/env python3
"""File naming convention for tax documents.

The benchmark uses this pattern:
  {year} {Form Type} - {Issuer} {Account Details} - {Description}.{ext}

Examples:
  2025 Form 1099 Composite - Schwab Acct 1965.pdf
  2025 Form 1099 Consolidated - E-Trade Acct 0060 - DoorDash Stock Plan.pdf
  2025 Stock Plan Transactions Supplement - E-Trade Acct 0060 - All RSU No ISO.pdf
  2025 Form 1098 - JPMorgan Chase - Houston Primary Residence Mortgage $30,312.pdf
  2025 Property Tax Bill - Fort Bend County - Primary Residence - $9,757 PAID.pdf
  2025 W-2 - DoorDash - Aditya - Wages $582,911.pdf
  2025 Form 1095-C - DoorDash - Health Coverage.pdf
  2025 K-1 - MH Sienna Retail II LLC - Aditya.pdf
"""

__all__ = ["format_tax_filename"]


def format_tax_filename(
    year: int,
    form_type: str,
    issuer: str,
    *,
    account: str = "",
    description: str = "",
    amount: str = "",
    status: str = "",
    ext: str = "pdf",
) -> str:
    """Build a filename matching the benchmark naming convention.

    Args:
        year: Tax year (e.g. 2025)
        form_type: Form type (e.g. "Form 1099 Composite", "W-2", "Property Tax Bill")
        issuer: Issuing entity (e.g. "Schwab", "Fort Bend County")
        account: Optional account identifier (e.g. "Acct 1965")
        description: Optional description (e.g. "DoorDash Stock Plan")
        amount: Optional dollar amount (e.g. "$30,312")
        status: Optional status (e.g. "PAID")
        ext: File extension without dot (default: "pdf")
    """
    parts = [str(year), form_type, "-", issuer]

    if account:
        parts.append(account)

    if description:
        parts.extend(["-", description])

    if amount:
        parts.extend(["-", amount])

    if status:
        parts.append(status)

    name = " ".join(parts)
    name = name.replace(" - - ", " - ").replace("  ", " ")
    return f"{name}.{ext}"
