# Jarvis — Start Here (agents & humans)

This file is the **entry point** for anyone — a human on a fresh machine, a new Cursor chat, or a
Cursor cloud agent — picking up work on this repo. It is intentionally short and points you to the
authoritative doc for whatever you're doing. **Read this first, then jump to the linked doc.**

> **Goal of this repo's docs:** the git repository is the single, self-sufficient source of truth.
> You should be able to clone it on any machine (or open it as a cloud agent) and continue improving
> the system **without** re-discovering anything from a past chat. If you learn something that isn't
> written down, write it down (see [Keeping docs current](#keeping-docs-current)).

---

## What this is

Jarvis is a suite of domain agents plus shared skills:

| Agent | Purpose | Deployment surface | Read |
|---|---|---|---|
| **BHAGA** | Tip allocation, payroll prep, labor model, forecasting, review bonuses | **Cloud-primary** (GCP Cloud Run; laptop retired 2026-05-29) | **[`RUNBOOK.md`](RUNBOOK.md)** first, then [`.cursor/rules/bhaga.md`](.cursor/rules/bhaga.md) + [`agents/bhaga/scripts/README.md`](agents/bhaga/scripts/README.md) |
| CHITRA | Tax document collection & organization | Local / laptop | [`.cursor/rules/chitra.md`](.cursor/rules/chitra.md) |
| CHANAKYA | Product research, market analysis, strategy | Local / laptop | [`.cursor/rules/chanakya.md`](.cursor/rules/chanakya.md) (+ `agents/chanakya/`) |
| AKSHAYA | Inventory forecasting & ordering | Local / laptop | [`.cursor/rules/akshaya.md`](.cursor/rules/akshaya.md) |

`skills/` holds reusable capabilities (Slack, Google Sheets/Drive, browser/Playwright, credentials,
Square, ADP, tip allocation/ledger). `core/` holds shared config/auth. Agents are glue; skills do
the work.

---

## Deployment surfaces — don't get this wrong

- **BHAGA runs in the cloud.** The nightly pipeline is a **Cloud Run Job** (`bhaga-daily-refresh`)
  triggered by Cloud Scheduler at 21:30 CT; OTP/READY round-trips go through a **Cloud Run webhook**
  (`bhaga-webhook`) + Firestore + Slack. The **laptop is retired** — there is no local Slack
  listener, no `/tmp/jarvis-*.json`, no launchd job for BHAGA. **Everything BHAGA-operational is in
  [`RUNBOOK.md`](RUNBOOK.md).**
- **CHITRA / CHANAKYA / AKSHAYA still run locally** on the laptop with the Slack-listener / `/tmp`
  inbox pattern described in [`.cursor/rules/jarvis.md`](.cursor/rules/jarvis.md). That pattern
  applies to **those** agents, **not** BHAGA.

If a doc tells you to "start the Slack listener" or "poll `/tmp/jarvis-pending-actions.json`" while
you're working on **BHAGA**, ignore it — that's laptop-era guidance for the other agents.

---

## Documentation map

| If you want to… | Read |
|---|---|
| Operate / debug the live BHAGA cloud system | [`RUNBOOK.md`](RUNBOOK.md) — architecture, Cloud Run units, sheets, scheduler, secrets, **Operating rules**, **Common tasks** |
| Understand the BHAGA domain (orders, items, labor, hourly vs full-time, KDS, tips, reviews, every metric) | [`agents/bhaga/knowledge-base/DOMAIN.md`](agents/bhaga/knowledge-base/DOMAIN.md) — data dictionary |
| Understand or change the BHAGA pipeline code | [`agents/bhaga/scripts/README.md`](agents/bhaga/scripts/README.md) — script-by-script + **Extending the model** |
| Know BHAGA's behavioral rules & invariants | [`.cursor/rules/bhaga.md`](.cursor/rules/bhaga.md) |
| Coordinate across agents / add an agent or skill | [`.cursor/rules/jarvis.md`](.cursor/rules/jarvis.md) |
| See project state / history / decisions | [`PROGRESS.md`](PROGRESS.md) |
| Add columns or a new tab to BHAGA sheets | [`agents/bhaga/scripts/README.md`](agents/bhaga/scripts/README.md) § Extending the model |
| Decide x-device tooling (Cursor cloud vs Claude) | [`docs/research/cursor-vs-claude-code-anywhere.md`](docs/research/cursor-vs-claude-code-anywhere.md) |

---

## Repo-wide rules (apply to all agents)

These travel with the repo. (Machine-local rules under `~/.cursor/rules` and `~/.cursor/skills`
also apply when working **on the operator's Mac**, but are **not** visible to cloud agents or other
machines — so anything that must survive everywhere lives here or in the linked docs.)

1. **Prod flows: commit → push → deploy. Never run prod against local-only or unpushed code.**
   For BHAGA the deployed artifact is a container image built by `.github/workflows/deploy.yml` on
   push to `main`. A local edit does nothing in prod until it's pushed and the image redeploys.
   (See `RUNBOOK.md` § Operating rules.)
2. **No PII / secrets in git.** Tokens, credentials, and personal data live in Secret Manager (cloud)
   or Keychain / gitignored `config.yaml` (local) — never committed.
3. **Skills are generic, agents are glue.** Reusable logic goes in `skills/`; agents only orchestrate.
4. **Config-driven, no hardcoding.** Sheet IDs, store profile, pay schedule, etc. come from
   `agents/bhaga/knowledge-base/store-profiles/<store>.json` (BHAGA) or `config.yaml`.
5. **Third-party portal automation uses the `user-playwright` MCP**, never `cursor-ide-browser`.

---

## Working from any machine / as a cloud agent

You do **not** need the operator's laptop. With GitHub + GCP access you can operate and extend BHAGA
entirely from the repo:

- **Operate:** follow `RUNBOOK.md` (gcloud commands for the job, scheduler, logs, Firestore markers).
- **Change code:** edit → run tests (`python3 -m pytest agents/bhaga/scripts/ cloud/ core/`) → commit
  → push `main` → GitHub Actions builds & deploys → verify per `RUNBOOK.md`.
- **No machine-local skill/rule is required** for BHAGA work; if you find you needed one, that's a
  gap — copy the needed knowledge into the repo.

---

## Keeping docs current

This is the mechanism that keeps the repo "rich and self-updating" (the whole point of these docs).
**When you change behavior, update the doc in the same change.** Lock-step targets:

| You changed… | Also update… |
|---|---|
| BHAGA pipeline behavior, a step, OTP flow, sheets, secrets, scheduler | `RUNBOOK.md` |
| A BHAGA script, the data flow, or how to extend the model | `agents/bhaga/scripts/README.md` |
| A BHAGA invariant / behavioral rule | `.cursor/rules/bhaga.md` |
| Cross-agent architecture, added an agent/skill | `.cursor/rules/jarvis.md` + this file's tables |
| Anything notable (status, decision, blocker) | `PROGRESS.md` (dated entry) |

Rule of thumb: **if a future you (or a cloud agent) would have to re-derive it from a chat, it
belongs in one of the files above.** A doc commit that lags the code is a bug.

**Enforcement (so this isn't just prose):**
- [`.cursor/rules/doc-maintenance.md`](.cursor/rules/doc-maintenance.md) auto-loads whenever you edit
  code under `agents/`, `skills/`, `cloud/`, `core/` and reminds you which doc to update.
- [`scripts/check_doc_freshness.py`](scripts/check_doc_freshness.py) is a deterministic checker that
  maps changed code paths → expected docs. Run it before finishing a change:
  ```bash
  python3 scripts/check_doc_freshness.py            # uncommitted work
  python3 scripts/check_doc_freshness.py --base origin/main   # a branch / push range
  ```
  It's a nudge by default (`--strict` to make it fail). **When you add a new code↔doc dependency,
  add a coupling to that script** so the checker keeps reflecting reality.
