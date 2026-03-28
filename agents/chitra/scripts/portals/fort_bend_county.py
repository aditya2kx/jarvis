#!/usr/bin/env python3
"""County property tax automation — CHITRA Playwright MCP instructions.

Public site, NO login required. Two data sources per county:
1. County Appraisal District (CAD) — appraisal values, exemptions, property details
2. County Tax Assessor — actual tax bills, payment history, receipts

Example: Fort Bend County TX uses esearch.fbcad.org (CAD) and fortbendtax.org (tax bills).
Adapt the URLs and search flows for the user's actual county.

Usage by CHITRA:
    1. Derive county from user's address (address → city/county lookup)
    2. Find the county's CAD and tax assessor websites
    3. Search by address to find the property
    4. Extract property data for tax filing
    5. Download tax bill/receipt if available
"""

PORTAL = "County Property Tax"
LOGIN_REQUIRED = False

STEPS = """
## Step 1: Derive county from address
- Use the user's address from their profile
- Map city/ZIP to county (e.g. many TX cities map to specific counties)
- Find the county's appraisal district search URL

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
session = PortalSession("{County} Property Tax")
session.stage_download(receipt_path, doc_type="Property Tax Receipt", issuer="{County} Tax Assessor")
session.upload_all()
```

## Known Issues
- Some county sites use Cloudflare bot protection (returns 403)
  - Workaround: use the CAD search site instead of the main county site
  - Some CAD sites (.org) are less protected than county .gov sites
- Property data is public information — no login needed
- Tax bill PDFs may require navigating through a payment portal
"""
