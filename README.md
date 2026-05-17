# Jarvis — AI Agent Coordinator

Jarvis is a personal AI agent framework that coordinates domain-specific agents and shared skills. Built for use with [Cursor IDE](https://cursor.sh/).

## Architecture

```
Jarvis/
├── .cursor/rules/       Cursor rules (coordinator + agent-specific)
├── core/                Shared infrastructure (config, auth)
├── skills/              Modular capabilities shared across agents
│   ├── slack/                   Send messages, receive replies, OTP flows
│   ├── google_drive/            Upload, list, delete files
│   ├── google_sheets/           Create and populate spreadsheets
│   ├── browser/                 Playwright-based portal automation
│   ├── credentials/             macOS Keychain registry
│   ├── gmail/                   Read, search, send Gmail messages
│   ├── pdf/                     Download and extract text from PDFs
│   ├── square_tips/             Daily tip totals from Square Payments API (BHAGA)
│   ├── adp_run_automation/      Per-employee daily hours from ADP RUN (BHAGA)
│   ├── tip_pool_allocation/     Pure-function pool-by-day fair share math (BHAGA)
│   └── tip_ledger_writer/       Tip ledger + ADP paste-block writer (BHAGA)
├── agents/              Domain-specific agents
│   ├── chitra/          Tax preparation agent
│   │   ├── knowledge-base/  Schemas, examples, portal playbooks
│   │   ├── scripts/         Agent-specific scripts
│   │   └── README.md
│   ├── chanakya/        Product research & strategy agent
│   │   └── README.md
│   ├── akshaya/         Inventory forecasting & ordering agent
│   │   ├── knowledge-base/  Vendor data, store profiles, schemas
│   │   ├── scripts/         Forecasting and extraction scripts
│   │   └── README.md
│   └── bhaga/           Tip allocation & payroll prep agent
│       ├── knowledge-base/  Schemas, store profiles, ADP selectors, learnings
│       ├── scripts/         Tip pull + allocation + sheet write orchestration
│       └── README.md
├── config.template.yaml Configuration template (tracked)
├── config.yaml          Your config (gitignored)
└── PROGRESS.md          Session continuity tracker
```

### What's Committed vs. Gitignored

| Committed (public) | Gitignored (local only) |
|---|---|
| Directory structure, scripts, rules | `config.yaml` (API keys, IDs) |
| Schemas, examples, playbooks | `agents/*/knowledge-base/*.json` (PII) |
| `config.template.yaml` | `credentials/`, `browser-profile/` |
| `portals.template.yaml` | `extracted/`, downloaded documents |

## Agents

### CHITRA (Tax Preparation)

Automates US federal/state tax document collection, organization, and CPA communication. See [agents/chitra/README.md](agents/chitra/README.md).

### CHANAKYA (Product Research & Strategy)

Researches markets, gathers operational data, builds financial models, and synthesizes strategic proposals. See [agents/chanakya/README.md](agents/chanakya/README.md).

### AKSHAYA (Inventory Forecasting & Ordering)

Pulls inventory data from ClickUp, order/recipe data from Square, cross-references with HQ supplies, forecasts demand, and outputs a living Google Sheet with reorder quantities and timing. See [agents/akshaya/README.md](agents/akshaya/README.md).

### BHAGA (Tip Allocation & Payroll Prep)

Pulls daily card tips from Square, daily clock-in/out hours per employee from ADP RUN (Playwright — no API for RUN), computes pool-by-day fair allocation, and writes a Google Sheet that doubles as the working ledger and produces a paste-ready block for ADP Time Sheet Import. See [agents/bhaga/README.md](agents/bhaga/README.md).

### Agent Naming Convention

Agents are named after figures from Sanskrit/Hindu mythology and Indian history whose role matches the agent's purpose (Chitragupta = divine record-keeper → tax agent; Chanakya = economist-strategist → research agent).

## Skills

| Skill | What it does | Setup |
|-------|-------------|-------|
| **Slack** | Messages, DMs, OTP flow | [skills/slack/README.md](skills/slack/README.md) |
| **Google Drive** | File upload, listing, deletion | Configured via `config.yaml` auth section |
| **Google Sheets** | Spreadsheet creation and population | Same auth as Drive |
| **Browser** | Playwright portal automation | Requires Playwright MCP in `.cursor/mcp.json` |
| **Credentials** | macOS Keychain registry for portal/API secrets | [skills/credentials/](skills/credentials/) |
| **Gmail** | Read, search, send Gmail messages | OAuth (same flow as Drive) |
| **PDF** | Download from Drive + text extraction | Requires `pdfplumber` (`pip install pdfplumber`) |
| **Square Tips** | Daily tip totals from Square Payments API | [skills/square_tips/README.md](skills/square_tips/README.md) — requires Square access token in Keychain |
| **ADP RUN Automation** | Per-employee daily hours from ADP RUN Time Tracker | [skills/adp_run_automation/README.md](skills/adp_run_automation/README.md) — Playwright + ADP login in Keychain |
| **Tip Pool Allocation** | Pure-function pool-by-day fair share math | [skills/tip_pool_allocation/README.md](skills/tip_pool_allocation/README.md) |
| **Tip Ledger Writer** | Tip ledger + ADP paste-block writer for Google Sheets | [skills/tip_ledger_writer/README.md](skills/tip_ledger_writer/README.md) |

## Quick Start

1. **Clone and configure:**
   ```bash
   git clone git@github.com:aditya2kx/jarvis.git
   cd jarvis
   cp config.template.yaml config.yaml
   # Edit config.yaml with your Google API IDs, auth paths, etc.
   ```

2. **Set up Google auth** (if using Drive/Sheets skills):
   Follow the Cursor Google MCP setup guide to create OAuth credentials.

3. **Set up Slack** (optional):
   See [skills/slack/README.md](skills/slack/README.md) for app creation and token storage.

4. **Store secrets in Keychain** (macOS):
   ```bash
   security add-generic-password -a SLACK_BOT_TOKEN -s jarvis -w "xoxb-YOUR-TOKEN"
   ```

5. **Open in Cursor** and start chatting — Jarvis rules auto-load from `.cursor/rules/`.

## Adding a New Agent

1. Create `agents/<name>/` with `knowledge-base/`, `scripts/`, `README.md`
2. Add a Cursor rule at `.cursor/rules/<name>.md`
3. Import shared infra: `from core.config_loader import ...`
4. Use skills: `from skills.slack.adapter import send_message`

## Adding a New Skill

1. Create `skills/<name>/` with `__init__.py`, main module, `README.md`
2. Import config: `from core.config_loader import ...`
3. If the skill needs secrets, document in `config.template.yaml` and store in Keychain

## Session Continuity

Jarvis uses `PROGRESS.md` to maintain state across conversations. Every session reads it first and updates it at the end. This eliminates dependence on conversation history.

## Credential Security

- **Passwords**: macOS Keychain via `security find-generic-password`
- **API tokens**: Keychain or gitignored `config.yaml`
- **OAuth**: Managed by Cursor's MCP auth flow
- **Zero secrets in git**: Enforced by `.gitignore` + pre-commit awareness

## License

MIT
