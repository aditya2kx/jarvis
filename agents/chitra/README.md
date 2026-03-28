# CHITRA — Tax Preparation Agent

**Chitragupta** (CHITRA) is a tax preparation agent that automates document collection, organization, CPA communication, and tax estimation for personal US federal and state returns.

## What CHITRA Does

- Collects tax documents from financial portals (brokerages, employers, county offices)
- Organizes documents in Google Drive with a standardized folder structure
- Maintains a document registry tracking what's received, pending, and missing
- Populates a Google Sheet dashboard for CPA collaboration
- Drafts CPA emails and manages Q&A workflows
- Estimates tax liability based on prior year returns and current year data

## Knowledge Base

Located at `agents/chitra/knowledge-base/`:

| Directory | Contents |
|-----------|----------|
| `schema/` | Markdown schemas defining JSON structures (document registry, tax profile, estimates, Drive conventions) |
| `examples/` | Anonymized example JSON files showing expected data shapes |
| `portal-playbooks/` | YAML files with step-by-step browser automation instructions for each financial portal |
| `*.json` | Actual tax data files (gitignored — contains PII) |

## Skills Used

- **google_drive** — Upload documents, list folder contents
- **google_sheets** — Populate the tax tracker dashboard
- **browser** — Automated portal logins and document downloads
- **slack** — OTP notifications during portal automation
- **pdf** — Extract text from tax documents for analysis

## Cursor Rules

CHITRA's behavior is defined in three rule files at `.cursor/rules/`:
- `chitra.md` — Core persona, routing logic, operational rules
- `chitra-workflows.md` — Templates for CPA email, change management, estimation
- `chitra-playbook.md` — Comprehensive operational procedures and gotchas
