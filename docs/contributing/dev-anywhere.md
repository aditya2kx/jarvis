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
| See the console change | `npm run dev` on localhost | **per-PR preview** `operator-console-pr<N>` behind prod IAP (same Google sign-in + allowlist), screenshot comment on the PR; the agent itself opens it with a token (`scripts/console_preview.py`, no Google sign-in); deleted on PR close + nightly sweep (`operator-console-preview.yml`) — Mac PRs get one too |
| Prod reads (BQ, logs, status) | ADC | `/prod-read …` PR comment → result encrypted to the agent's key (`scripts/prod_read.py`; repo is public) |
| Prod mutations (env flags, job runs, migrations) | ADC / Cloud Shell | `/prod-ops …` PR comment → operator approves in GitHub Environment `prod` (`scripts/prod_ops.py`) |
| Git identity | `GH_TOKEN` (bot PAT, Keychain) → commits + pushes as `jarvis-agent-bot328` | same PAT, no copy: `scripts/cloud_git_identity.sh` (environment `start`) reads Secret Manager `github-bot-pat` via Cursor OIDC ([gcp-access.md](gcp-access.md) recipe F), logs `gh` in, drops Cursor's app-token rewrite, sets bot name/email (a `GH_TOKEN` Cursor secret also works) |
| Open the PR | `gh pr create` as the bot | push a `cursor/*` branch → `cloud-agent-pr.yml` opens a draft PR as the bot (`ADMIN_PAT`); the agent sets title/body with `ManagePullRequest` `update_pr` — never its `create_pr`, which opens under the operator's account (Issue #321) |
| Claude review | normal | normal — the actor is the bot, as on the Mac |
| Build cost → BQ | `pr_cost_ledger.py capture-build` from transcripts | PR body line `Cursor-Agent: <bc-id> model=<model-id>` (add `prs=<a>,<b>` when one agent built several PRs — the cost splits by diff size; one line per agent when a later agent continues the PR); `pr-cost-gate.yml` prices the agent's `/usage` |
| Ad-hoc logins (portals, Cloud Shell, vendor dashboards) | Keychain + `user-playwright` | 1Password vault `Shared with Bots` via `skills/vault` (`type` into the focused field — never printed) |
| Babysit | `dev_event_listener` + hooks | subscribe to the PR's review/CI events; `@cursor` comments reach the agent |

Why the Mac cannot be disturbed: `/jarvis-new-task` and its `intake-signal` job are byte-identical
to before; `/jarvis-cloud-task`, `/prod-read` and `/prod-ops` are excluded from `comment-signal`, so
the Mac listener never sees them; a Cloud Agent's `cursor/*` branch has no Mac phase cache, so its CI
signals route as `unrouted` (skipped). `detect_surface()` returns `mac_local` on macOS even inside a
Cursor desktop agent terminal (which also sets `CURSOR_AGENT=1`).

## Handoff — Mac ↔ phone, either direction, mid-PR

One PR, one owner at a time; the PR (branch + body + a handoff note comment) carries the work, the
tracking issue carries phase state. Chat history does not travel, so the note is the next agent's
context. Type **`/jarvis-handoff`** in the leaving chat (`.cursor/skills/jarvis-handoff`); it commits
and pushes, writes the note, and runs `scripts/handoff.py out`.

| | Mac → phone (`/jarvis-handoff cloud`) | Phone → Mac (`/jarvis-handoff mac`) |
|---|---|---|
| Leaving agent | WIP commit + push; `handoff.py out --to cloud`: captures the Mac session's build cost (`capture-build`), posts the note, marks the phase cache `handed_off_to=cloud` (the Mac listener then returns `handed_off` for this branch instead of waking the idle Mac chat), comments `/jarvis-cloud-task` on the PR | `cloud_git_identity.sh` + push; `handoff.py out --to mac` posts the note; the agent stops and unsubscribes |
| Start the other side | automatic: `cloud-intake` sees the comment is on a PR → Cursor API `prUrl` + `workOnCurrentBranch` (same branch, agent mode, no new PR), appends its `Cursor-Agent:` line to the PR body, comments the agent link → open it in Cursor mobile | on the Mac, in any Jarvis chat: `python3 scripts/handoff.py in --pr <N>` → worktree on the PR's branch, Cursor window seeded with "continue PR #N" |
| Arriving agent | `handoff.py in --pr <N>`: checks out/pulls the branch, restores the phase cache from the tracking issue (`Closes #N` → its `Done:` line), clears `handed_off_to`, prints the latest note | same command (`--no-open` inside the seeded chat) |
| Cost | Mac rows from `capture-build` + the agent's `Cursor-Agent:` row; `pr-cost-gate` sums both | the agent's row is already there; the Mac session adds `capture-build` as usual |

Phone → phone needs nothing: comment `/jarvis-cloud-task <what next>` on the PR to start a fresh
Cloud Agent on its branch. Cursor's own **Move to Cloud** (desktop Agents Window) also carries the
chat itself — still commit/push first and have the cloud agent run `handoff.py in`; **Move to
Local** is often unavailable for agents that started in the cloud, hence `handoff.py in` on the Mac.

## Console preview (read-only)

`CONSOLE_PREVIEW=1` (`apps/operator-console/lib/config/preview.ts`) is set only on preview services:
middleware returns 403 for every non-GET/HEAD (server actions and API routes), the Accounting
on-load Plaid dedupe and the order-recommendation write are skipped, and the top bar shows a
**Preview** badge. Previews run as the prod runtime SA against prod BQ, so read-only is the
isolation. Previews are separate services — never a revision of `operator-console` — so the flag
cannot reach prod; `/prod-ops` refuses to set it. Unset (localhost, prod) the console behaves as
before. Screenshots cover `/home` and `/inventory` only — Payroll and Accounting carry names, wages
and bank lines, and this repo is public.

Previews verify IAP's JWT against their own service (`IAP_SERVICE_NAME=operator-console-pr<N>`),
and each build carries `NEXT_DEPLOYMENT_ID`, so a tab left open across a redeploy hard-reloads
instead of mixing builds. The CI identity is added to the **preview's** IAP list only, so the `smoke`
job taps every mobile menu item on the real preview in iPhone WebKit and fails on a stuck tap or a
page error (paths and timings only in the log).

## Operator one-time setup

| Step | Where | Why |
|---|---|---|
| Vault `Shared with Bots` + read-only service account → Cursor secret `OP_SERVICE_ACCOUNT_TOKEN` ([skills/vault](../../skills/vault/README.md#operator-one-time-setup)); put the Google owner login (+ authenticator code) in it | 1Password + Cursor dashboard | agents sign in to sites by themselves |
| `bash scripts/provision_cloud_agent_identity.sh` (Cursor OIDC trust + gateway secrets) — **the agent runs it** in Cloud Shell as `aditya.2ky@gmail.com` (vault item `Google (aditya.2ky)`). Done 2026-10-11 ([gcp-access.md](gcp-access.md) recipe F) | Cloud Shell | Cloud Agents act as `jarvis-agent-bot328` and open previews with tokens; gateway deployable |
| Create environment `prod` with yourself as **Required reviewer** | GitHub → Settings → Environments (owner-only; works on phone) | `/prod-ops` refuses until it exists |
| Save the proposed environment config | Cursor dashboard → Cloud Agents → Environment | new agents boot with deps installed |
| Copy `jarvis-dev-gateway-token` into Siri Shortcut / Grok | Cloud Console → Secret Manager | voice / Grok entry |

`/prod-read logs` also needs `roles/logging.viewer` for the CI deploy identity (it has none today):
`gcloud projects add-iam-policy-binding jarvis-bhaga-prod --member serviceAccount:<WIF_SERVICE_ACCOUNT> --role roles/logging.viewer`.

Optional: repo variable `CLOUD_AGENT_MODEL` picks the Cloud Agent model (account default otherwise).

## Still Mac-only

The Keychain-backed secrets the runtime scripts read and CHITRA/CHANAKYA/AKSHAYA (local agents).
Portal provisioning moves to the cloud once the portal's login is in the vault: the agent drives the
VM's desktop browser and types credentials with `skills/vault`. BHAGA runtime is already cloud (`RUNBOOK.md`).
