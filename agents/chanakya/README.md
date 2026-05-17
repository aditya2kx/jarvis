# CHANAKYA — Product Research & Strategy Agent

**Chanakya** (चाणक्य) — named after the legendary Indian economist, strategist, and author of the Arthashastra (the science of wealth). Master of market analysis, resource optimization, and competitive strategy who built empires from first principles.

## What CHANAKYA Does

- Researches products, markets, and competitive landscapes
- Gathers operational data from store systems (POS, inventory, vendors)
- Collects and organizes intelligence from Google Drive, Sheets, Gmail, and web sources
- Builds financial models and business cases with real operator data
- Synthesizes findings into strategic proposals and recommendations
- Tracks knowledge across sessions for long-running research projects

## Knowledge Base

Located at `agents/chanakya/knowledge-base/`:

| Directory | Contents |
|-----------|----------|
| `schema/` | Markdown schemas defining research project structures |
| `examples/` | Anonymized example outputs (proposals, models) |
| `*.json` | Active research data (gitignored — may contain business-sensitive info) |

## Skills Used

- **google_drive** — Search and read research documents, vendor invoices, business files
- **google_sheets** — Pull operational data (sales, inventory, financials)
- **gmail** (planned) — Search vendor communications, HQ emails, invoice attachments
- **browser** — Web research, product demos, competitive analysis
- **slack** — Async communication during long research sessions
- **pdf** — Extract data from invoices, reports, contracts

## Agent Naming Convention

Jarvis agents are named after figures from Sanskrit/Hindu mythology and Indian history whose role matches the agent's purpose:

| Agent | Named After | Role |
|-------|------------|------|
| CHITRA | Chitragupta — divine scribe, keeper of all records | Tax document collection and organization |
| CHANAKYA | Chanakya — economist, strategist, author of Arthashastra | Product research, market analysis, business strategy |

Future agent names should follow this convention. Other candidates considered:
- **Narada** (cosmic messenger, intelligence gatherer) — good for a pure information-retrieval agent
- **Vidura** (wisest advisor in Mahabharata) — good for a decision-support/advisory agent

## Cursor Rules

CHANAKYA's behavior will be defined at `.cursor/rules/chanakya.md`.
