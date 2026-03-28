#!/usr/bin/env python3
"""County property tax automation template — CHITRA Playwright MCP instructions.

Copy this file and rename for each county (e.g. my_county.py).
These files are gitignored — they stay local since they reveal your location.

Public sites, typically NO login required. Two data sources per county:
1. County Appraisal District (CAD) — appraisal values, exemptions, property details
2. County Tax Assessor — actual tax bills, payment history, receipts

Usage by CHITRA:
    1. Derive county from user's address (city/ZIP → county lookup)
    2. Find the county's CAD and tax assessor websites
    3. Search by address to find the property
    4. Extract property data for tax filing
    5. Download tax bill/receipt if available
"""

# Fill these in for each county:
PORTAL = "{County Name} Property Tax"
CAD_URL = "https://search.{county}cad.org/"
TAX_URL = "https://www.{county}tax.org/"
LOGIN_REQUIRED = False

STEPS = """
## Step 1: Derive county from address
- Use the user's address from their profile
- Map city/ZIP to county (many online tools, or hardcode known mappings)
- Find the county's appraisal district (CAD) search URL
- Find the county's tax assessor website

## Step 2: Search property on County Appraisal District
- browser_navigate to the county's property search URL
- Click "By Address" tab (most CAD sites have this)
- Fill Street Number and Street Name fields
- Click Search
- Wait 3s for results

## Step 3: Extract property data
From the results page, extract:
- Property ID / Quick Ref ID
- Owner name(s)
- Situs address
- Appraised value (improvements + land)
- Exemptions (homestead, over-65, disability, etc.)
- Deed history (purchase date, prior owner)
- Taxing jurisdictions and rates
- Property details (sqft, year built, lot size)

## Step 4: Get tax bill from County Tax Assessor
- Navigate to the county's tax assessor website
- Search by property ID or address
- Find tax bill for the relevant year
- Download receipt/statement if available

## Step 5: Stage and upload
```python
from skills.browser.portal_session import PortalSession
session = PortalSession("{County} Property Tax")
session.stage_download(receipt_path, doc_type="Property Tax Receipt", issuer="{County} Tax Assessor")
session.upload_all()
```

## Known Issues
- Some county .gov sites use Cloudflare bot protection (returns 403)
  - Workaround: use the CAD search site (.org) which is often less protected
- Property data is public information — no login needed
- Tax bill PDFs may require navigating through a payment portal
- Some counties combine city/county/school taxes; others have separate sites
"""
