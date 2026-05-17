# AKSHAYA — Inventory Forecasting & Ordering Agent

**Akshaya** (अक्षय) — named after the **Akshaya Patra**, the divine vessel given to the Pandavas by Surya (the Sun god). It produced an unlimited supply of food and would never run empty until the last person had eaten. "Akshaya" means "inexhaustible" or "that which never diminishes."

*The kitchen never runs empty when AKSHAYA is watching.*

## What AKSHAYA Does

- Pulls manual inventory counts from ClickUp (#running-austin-palmetto)
- Extracts order history and recipe/ingredient data from Square (POS)
- Cross-references with HQ-supplied items (centrally controlled by franchise)
- Projects future demand based on order trends and seasonality
- Outputs a living Google Sheet with: projected inventory needs, reorder quantities, and timing for HQ orders
- Detects emergency restocking patterns (local grocery panic-buys) and adjusts safety stock

## Design Principles

- **Each data source extraction is its own skill** — reusable across stores and franchises
- **Forecasting logic is a separate orchestrating skill** — composable with any data source
- **Output is a living sheet** — refreshable on demand, not a one-time export
- **Multi-store ready** — works for Austin now, expandable to Houston (September 2026) and beyond

## Knowledge Base

Located at `agents/akshaya/knowledge-base/`:

| Directory | Contents |
|-----------|----------|
| `schema/` | Data schemas for inventory, recipes, orders |
| `vendor-data/` | HQ item lists, vendor SKUs, pricing, lead times |
| `store-profiles/` | Per-store config (location, suppliers, delivery schedules) |
| `*.json` | Active forecasting data (gitignored — may contain business-sensitive info) |

## Data Sources

### ClickUp: #running-austin-palmetto
- Manual inventory reporting by store staff
- Daily/shift-level counts of ingredients on hand
- MCP: `user-clickup`

### Square POS
- All orders with item-level detail, modifiers, Build Your Own customizations
- Recipe/ingredient structure (menu items → ingredients)
- Channel attribution (in-store, DoorDash Storefront, DoorDash Marketplace, Uber Eats, Kiosk)
- ~42 orders/day (soft opening), targeting 100+ at scale
- Access via: `user-playwright` (browser automation) or Square API

### HQ-Supplied Items
- Acai (managed via co-founder Slack DMs, delivered by driver)
- Branded packaging, granola, specialty items from central warehouse
- Vendor master list with SKUs, pricing, and expense breakdowns
- Reference: `get open/proposal/03-vendor-and-cost-data.md`

### Google Sheets
- Software research sheet: `1rmctNH5Vdf5JRNnq5fUg4OYkYrSdkfBqM9OWOVQkeQo`
- Output forecasting sheets via `user-google-drive-sheets` and `user-palmetto-google`

## Skills Used

- **clickup** — Extract inventory reports and task data from ClickUp spaces
- **browser** — Navigate Square dashboard for order history and recipe data (Playwright)
- **google_sheets** — Read vendor data, write forecasting output sheets
- **google_drive** — Search and read operational documents
- **slack** — Async communication via AKSHAYA agent profile (Slack identity TBD)

## Key Operational Context

### Product Mix (from Austin soft opening data)
- **Build Your Own Bowl**: 28% of orders — hardest to forecast (40+ ingredient combinations)
- **Signature Bowls**: 34% — fixed recipes, predictable ingredient consumption
- **Smoothies**: 34% — fixed recipes, predictable
- Staff meals: ~4/day (consume ingredients but don't generate revenue — must be accounted for)

### Inventory Challenges
- Daily fresh fruit deliveries = high spoilage risk
- Acai sourced directly from Brazil = long international supply chain
- BYO customization makes ingredient-level demand prediction non-trivial
- Regional fruit sourcing diverges between CA and TX locations
- Emergency grocery runs (HEB, Central Market) signal inventory gaps

### Current Tooling
- MarketMan ($396 subscription) — existing inventory tool, known pain points TBD
- Square POS — all recipes and prices controlled by HQ

## Cursor Rules

AKSHAYA's behavior is defined at `.cursor/rules/akshaya.md`.

## Agent Naming Convention

Jarvis agents are named after figures from Sanskrit/Hindu mythology and Indian history whose role matches the agent's purpose:

| Agent | Named After | Role |
|-------|------------|------|
| CHITRA | Chitragupta — divine scribe, keeper of all records | Tax document collection and organization |
| CHANAKYA | Chanakya — economist, strategist, author of Arthashastra | Product research, market analysis, business strategy |
| AKSHAYA | Akshaya Patra — the inexhaustible divine vessel of food | Inventory forecasting, demand prediction, supply chain ordering |
