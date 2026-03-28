#!/usr/bin/env python3
"""County property tax navigation module — Playwright MCP steps.

Generic navigation logic for county appraisal district (CAD) and tax assessor
websites. Works for any US county. Contains zero user-specific data.

Most counties have two separate sites:
1. Appraisal District (CAD) — property values, exemptions, ownership records
2. Tax Assessor/Collector — tax bills, payment history, receipts

Quirks discovered during testing:
- County .gov main sites often use Cloudflare bot protection (403 errors)
- CAD search sites (.org) are typically less protected and work with Playwright
- Property data is public record — no login required
- Address search is the most reliable lookup method
"""

PORTAL = "County Property Tax"
LOGIN_REQUIRED = False

# Common URL patterns (substitute county name):
# CAD: https://esearch.{county}cad.org/
# Tax: https://www.{county}tax.org/
# Some counties use: https://propaccess.{county}cad.org/

SEARCH_STEPS = """
## Step 1: Determine the county
- User provides their address
- Map city/ZIP to county name
- Common resources: census.gov geocoder, USPS ZIP lookup
- Texas example pattern: {county}cad.org for appraisal, {county}tax.org for bills

## Step 2: Find the CAD search URL
- Search for "{County Name} appraisal district property search"
- Most Texas CADs use BIS Consultants (esearch.{county}cad.org)
- California uses county assessor sites
- Other states vary — some use statewide portals

## Step 3: Search by address
1. browser_navigate → county CAD search URL
2. Click "By Address" tab (most CAD sites have Search | By Owner | By Address | By ID)
3. Fill fields:
   - Street Number (e.g. "1234")
   - Street Name (e.g. "Main")
   - Leave other fields blank for broadest match
4. Click Search button
5. Wait 3s for results

## Step 4: Select property from results
- Results table shows: Property ID | Geo ID | Type | Owner | Address | Appraised Value
- Click on the matching property row
- Property detail page loads with full information
"""

EXTRACT_STEPS = """
## Data to extract from property detail page

### Account info
- Property ID / Quick Ref ID
- Geographic ID
- Property Type (Residential, Commercial, etc.)

### Location
- Situs Address
- Legal Description
- Subdivision

### Ownership
- Owner name(s)
- Ownership percentage
- Mailing address

### Values
- Improvement Homesite Value
- Land Homesite Value
- Market Value (total)
- Appraised Value
- Value history (multi-year table)

### Exemptions
- Homestead (HS)
- Over-65
- Disability
- Other exemptions

### Taxing jurisdictions
- Table showing: Entity | Description | Market Value | Taxable Value
- Common entities: County, City, ISD (school district), MUD, Drainage, Parks

### Deed history
- Deed dates, types, grantor/grantee
- Useful for confirming purchase date

### Improvements
- Building type, class, year built, sqft
- Number of stories, attached structures
"""

TAX_BILL_STEPS = """
## Step 5: Get tax bill from Tax Assessor site
1. Navigate to the county tax assessor website
2. Search by Property ID (from CAD) or address
3. Find tax statement for the relevant tax year
4. Download as PDF if available
5. Note payment status (paid/unpaid) and amounts

## Common tax assessor features
- Tax bill lookup by account number or property ID
- Payment history showing all years
- PDF download of tax statement/receipt
- Some require creating a free account to download
"""

KNOWN_ISSUES = """
- Cloudflare protection: Many .gov sites block automated browsers
  - Workaround: Use .org CAD search sites instead
  - Some sites work after a brief wait (Cloudflare challenge auto-solves)
- BIS Consultants sites (common in TX): reliable, consistent UI across counties
- California: uses county-specific assessor sites, less standardized
- Multi-county properties: rare but possible — check all applicable counties
- New construction: may not appear in CAD until next assessment cycle
- Exemption lag: homestead exemptions filed mid-year may not show until next year's roll
"""
