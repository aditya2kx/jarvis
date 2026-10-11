# Dev from anywhere — Mac or Cursor Cloud Agent

Jarvis development runs on two surfaces with the same gates and PR lifecycle. **The Mac flow is
unchanged** — every Mac command, script and listener behaves exactly as before; the cloud path is
additive and reached through its own commands, so both can be used side by side.
`scripts/gcp_access_probe.py` reports which one an agent is in (`dev_surface=mac_local` /
`cursor_cloud` / `ci`). Issue #384.

| Need | Mac (`mac_local`) | Cursor Cloud Agent (`cursor_cloud`, e.g. from Cursor mobile) |
|---|---|---|
| Start work | `/jarvis-new-task` → `new_requirement.py` worktree + Cursor window | `/jarvis-cloud-task` on an issue → `scripts/cloud_agent.py launch` (link commented on the issue); or open an agent on the repo in Cursor mobile/web; or Siri / Grok via the [gateway](../../cloud/jarvis_dev_gateway/README.md) |
| Environment | your checkout | `.cursor/environment.json` (pip + pytest + Playwright Chromium + console `npm ci`) |
| See the console change | `npm run dev` on localhost | **per-PR preview** `operator-console-pr<N>` behind prod IAP (same Google sign-in + allowlist), screenshot comment on the PR; deleted on PR close + nightly sweep (`operator-console-preview.yml`) — Mac PRs get one too |
| Prod reads (BQ, logs, status) | ADC | `/prod-read …` PR comment → result encrypted to the agent's key (`scripts/prod_read.py`; repo is public) |
| Prod mutations (env flags, job runs, migrations) | ADC / Cloud Shell | `/prod-ops …` PR comment → operator approves in GitHub Environment `prod` (`scripts/prod_ops.py`) |
| PR / comments | `gh` as the bot | `ManagePullRequest` tool (`gh` is read-only); pushes arrive as the `cursor` GitHub App |
| Claude review | normal | normal (`allowed_bots: cursor` in `claude-review.yml`) |
| Build cost → BQ | `pr_cost_ledger.py capture-build` from transcripts | PR body line `Cursor-Agent: <bc-id> model=<model-id>`; `pr-cost-gate.yml` prices the agent's `/usage` |
| Babysit | `dev_event_listener` + hooks | subscribe to the PR's review/CI events; `@cursor` comments reach the agent |

Why the Mac cannot be disturbed: `/jarvis-new-task` and its `intake-signal` job are byte-identical
to before; `/jarvis-cloud-task`, `/prod-read` and `/prod-ops` are excluded from `comment-signal`, so
the Mac listener never sees them; a Cloud Agent's `cursor/*` branch has no Mac phase cache, so its CI
signals route as `unrouted` (skipped). `detect_surface()` returns `mac_local` on macOS even inside a
Cursor desktop agent terminal (which also sets `CURSOR_AGENT=1`).

## Console preview (read-only)

`CONSOLE_PREVIEW=1` (`apps/operator-console/lib/config/preview.ts`) is set only on preview services:
middleware returns 403 for every non-GET/HEAD (server actions and API routes), the Accounting
on-load Plaid dedupe and the order-recommendation write are skipped, and the top bar shows a
**Preview** badge. Previews run as the prod runtime SA against prod BQ, so read-only is the
isolation. Previews are separate services — never a revision of `operator-console` — so the flag
cannot reach prod; `/prod-ops` refuses to set it. Unset (localhost, prod) the console behaves as
before. Screenshots cover `/home` and `/inventory` only — Payroll and Accounting carry names, wages
and bank lines, and this repo is public.

## Operator one-time setup

| Step | Where | Why |
|---|---|---|
| Create environment `prod` with yourself as **Required reviewer** | GitHub → Settings → Environments (owner-only; works on phone) | `/prod-ops` refuses until it exists |
| Save the proposed environment config | Cursor dashboard → Cloud Agents → Environment | new agents boot with deps installed |
| Create the gateway's two secrets + per-secret grants (7 lines) | Cloud Shell — [gateway README § One-time setup](../../cloud/jarvis_dev_gateway/README.md#one-time-setup-cloud-shell-operator) | deploy identity cannot create secrets |
| Copy `jarvis-dev-gateway-token` into Siri Shortcut / Grok | Cloud Console → Secret Manager | voice / Grok entry |

Optional: repo variable `CLOUD_AGENT_MODEL` picks the Cloud Agent model (account default otherwise).

## Still Mac-only

`user-playwright` MCP portal provisioning (ADP/Square/Slack app setup), Keychain-backed secrets, and
CHITRA/CHANAKYA/AKSHAYA (local agents). BHAGA runtime is already cloud (`RUNBOOK.md`).
