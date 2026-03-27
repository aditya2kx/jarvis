# CHITRA — AI-Powered Personal Tax Assistant

Named after **Chitragupta**, the divine record-keeper from Hindu mythology who maintains the eternal ledger of every soul's deeds.

CHITRA is an open-source framework for managing personal tax document collection, organization, and CPA communication using AI agents in [Cursor IDE](https://cursor.com). It activates automatically when you open this workspace in Cursor — just chat with it.

## What CHITRA Does

- **Organizes tax documents** in Google Drive with a numbered folder convention
- **Tracks document status** across 23+ document types (W-2s, 1099s, K-1s, property tax, etc.)
- **Maintains a Google Sheet dashboard** with document checklist, return summary, CPA questions, and a document navigator
- **Generates CPA-ready email drafts** with all documents zipped and a navigation guide
- **Runs tax estimates** using prior year returns as a baseline
- **Automates document retrieval** from financial portals via browser automation (Playwright MCP)
- **Handles OTP/2FA** interactively via Slack notifications

## Architecture

```
CHITRA (Cursor AI Agent)
├── Rules (.cursor/rules/)
│   ├── chitra.md          — Core persona, routing, cost discipline
│   ├── chitra-workflows.md — CPA email template, change management, estimation
│   └── chitra-playbook.md  — Year-start bootstrap, document workflows, error catalog
├── Scripts (scripts/)
│   ├── config_loader.py    — Shared config + auth helpers
│   ├── populate_sheet.py   — Google Sheet population from registry
│   ├── upload_to_drive.py  — Drive file upload
│   ├── portal_collector.py — Browser automation orchestrator
│   └── (utility scripts)
├── Knowledge Base (knowledge-base/)
│   ├── schema/             — JSON schema documentation (4 files)
│   ├── examples/           — Anonymized example JSON files
│   └── portal-playbooks/   — Per-portal browser automation YAML (12 portals)
└── Config
    ├── config.template.yaml — Configuration template (IDs, paths, profile)
    ├── portals.template.yaml — Portal credential template
    └── .cursor/mcp.json     — MCP server configuration
```

### What's committed vs. what stays local

| Committed (public) | Gitignored (local only) |
|---|---|
| Scripts, rules, schemas, playbooks | `knowledge-base/*.json` (PII/financials) |
| `config.template.yaml` | `config.yaml` (your real IDs) |
| `portals.template.yaml` | `credentials/portals.yaml` (your creds) |
| Example JSON files | `2025/`, `extracted/`, `*.pdf`, `*.csv` |
| Portal playbook YAMLs | `browser-profile/` (session cookies) |

## Quick Start

### 1. Prerequisites

- [Cursor IDE](https://cursor.com) with AI agent support
- Python 3.8+
- Google Drive + Sheets MCP server (configured via Cursor)
- `pdfplumber` Python package (for PDF extraction): `pip install pdfplumber`

### 2. Setup

```bash
# Clone the repo
git clone git@github.com:adi2ky/chitra.git
cd chitra

# Copy and fill in configuration
cp config.template.yaml config.yaml
# Edit config.yaml with your Google Sheet/Drive IDs

# Copy and fill in portal credentials (for browser automation)
mkdir -p credentials
cp portals.template.yaml credentials/portals.yaml
# Edit credentials/portals.yaml with your portal usernames

# Store passwords in macOS Keychain (never in files)
security add-generic-password -s schwab-tax -a YOUR_USERNAME -w YOUR_PASSWORD
security add-generic-password -s etrade-tax -a YOUR_USERNAME -w YOUR_PASSWORD
# ... repeat for each portal
```

### 3. Initialize a new tax year

Open the workspace in Cursor and ask CHITRA:

> "Bootstrap 2026 tax year — create Drive folders, initialize document registry, set up the Google Sheet"

CHITRA will follow the playbook in `.cursor/rules/chitra-playbook.md` to:
1. Create the `Taxes/2026/` folder tree in Google Drive
2. Initialize `document-registry.json` from your prior year profile
3. Create or update the Google Sheet with all tabs
4. Set up `estimates.json` with baseline from filed return

### 4. Collect documents

Ask CHITRA to check each portal:

> "Check Schwab for my 2025 tax documents"

CHITRA uses Playwright MCP to log in, navigate to the tax documents section, download PDFs, and upload them to the correct Drive folder. If OTP is needed, CHITRA sends you a Slack DM and waits for your reply.

### 5. Prepare for CPA

> "Generate the CPA email and document navigator"

CHITRA compiles the document navigator (one row per Drive folder), drafts the email, and updates the Google Sheet.

## Browser Automation

CHITRA can automatically download tax documents from financial portals using [Playwright MCP](https://github.com/microsoft/playwright-mcp).

### Supported Portals

| Portal | Login | OTP | Automated |
|--------|-------|-----|-----------|
| Schwab, E*Trade, Robinhood | Yes | SMS | Yes |
| Fidelity | Yes | SMS | Yes |
| Wells Fargo, JPMorgan Chase | Yes | SMS | Yes |
| Ziprent | Yes | Email | Yes |
| Fort Bend County, San Mateo County | No | N/A | Yes (public) |
| Chase Business, Homebase | N/A | N/A | No (CPA has access) |
| Yardi | TBD | TBD | Partial |

### How OTP Works

1. CHITRA navigates to the portal and enters credentials
2. Portal sends OTP to your phone
3. CHITRA sends you a Slack DM: "Portal is asking for a verification code. Please reply with the code."
4. You reply in Slack with the OTP
5. CHITRA enters it and proceeds with download

### Credential Security

- **Passwords**: Stored in macOS Keychain, retrieved via `security` CLI — never in plaintext
- **Usernames**: In `credentials/portals.yaml` (gitignored)
- **OTP**: Always interactive — never stored or automated
- **Browser sessions**: Persistent profile in `browser-profile/` (gitignored) minimizes re-login

## Knowledge Base Schemas

| Schema | Documents |
|--------|-----------|
| `document-registry.schema.md` | 23 document types, extractedData shapes, status machine |
| `tax-profile.schema.md` | Income/deduction/credit/tax structure, year-rollover procedure |
| `drive-folder-convention.md` | Numbered folder pattern (01-10), naming rules, suffixes |
| `estimates.schema.md` | Three-layer estimate architecture (baseline → professional → current) |

## Configuration Reference

### `config.yaml`

```yaml
auth:
  credentials_path: "~/.../google-mcp-auth/.gdrive-server-credentials.json"
  env_path: "~/.../google-mcp-auth/.env"

google_sheets:
  tax_tracker_id: "YOUR_SPREADSHEET_ID"
  iso_tracker_id: "YOUR_ISO_TRACKER_ID"

google_drive:
  taxes_root_id: "YOUR_TAXES_ROOT_FOLDER_ID"
  taxes_year_id: "YOUR_YEAR_FOLDER_ID"

profile:
  tax_year: 2025
  filing_status: "MFJ"
```

### Session Continuity

CHITRA uses `PROGRESS.md` to maintain context across conversations. On each new conversation, CHITRA reads this file first and updates it at the end. This eliminates reliance on conversation history.

## v2 Roadmap — Cloud Execution

The current architecture is **local-first** (requires laptop running) because:
- Playwright MCP runs a local Chromium instance
- macOS Keychain credentials are only accessible locally
- Persistent browser profile lives on local disk

Future paths to cloud execution:
- **Cursor Cloud Agents + Slack**: If Playwright MCP gets cloud support (or via a cloud browser service like Browserbase)
- **GitHub Actions + headless Playwright**: Scheduled workflow during tax season (Jan–Apr), GitHub Secrets for credentials, Slack for OTP
- **Key migration**: macOS Keychain → GitHub Secrets or AWS Secrets Manager

## License

MIT

## Contributing

This is a personal tax workflow framework. Contributions welcome for:
- Additional portal playbooks
- Improved schema documentation
- Better tax estimation models
- Cloud execution support
