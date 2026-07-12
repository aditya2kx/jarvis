# Jarvis — Start Here (agents & humans)

This file is the **entry point** for anyone on a fresh machine or in a new chat.
It is intentionally short and points to the authoritative doc for whatever you're doing.

> **Goal:** the git repository is the single, self-sufficient source of truth.
> You should be able to clone it on any machine and continue improving the system
> **without** re-discovering anything from a past chat.

---

## Consult before you plan or design

Read these first, derive proposals from them, cite what you used.

1. **[`CONTRIBUTING.md`](CONTRIBUTING.md)** — dev loop as success criteria; evidence-definition step; phase lifecycle.
2. **[`docs/WORKFLOW.md`](docs/WORKFLOW.md)** — canonical lifecycle map: 10 phases → 5 stages, agent hierarchy, operator-reserved zone, autonomy ladder, verification matrix, roadmap.
3. **[`.cursor/rules/bhaga-principles.mdc`](.cursor/rules/bhaga-principles.mdc)** (BHAGA work) / **[`.cursor/rules/chitra.mdc`](.cursor/rules/chitra.mdc)** (CHITRA work) — agent-specific invariants.
4. **[`RUNBOOK.md`](RUNBOOK.md)** — cloud operation (BHAGA only).

---

## Agents

| Agent | Purpose | Deployment | Read |
|---|---|---|---|
| **BHAGA** | Tip allocation, payroll prep, labor model | Cloud (GCP Cloud Run) | `RUNBOOK.md` + `bhaga.mdc` |
| **CHITRA** | Tax document collection | Local laptop | `chitra.mdc` + `chitra-playbook.mdc` |
| **CHANAKYA** | Product research, market analysis | Local laptop | `chanakya.mdc` |
| **AKSHAYA** | Inventory forecasting & ordering | Local laptop | `akshaya.mdc` |

---

## Repo-wide rules (all agents, all machines)

1. **Never push `main` directly** — every change lands via PR (branch → CI → operator squash-merge).
2. **No PII / secrets in git** — tokens and personal data in Secret Manager (cloud) or gitignored `config.yaml`.
3. **Skills are generic; agents are glue** — reusable logic → `skills/`; agents only orchestrate.
4. **Config-driven; no hardcoding** — Sheet IDs, store profiles, pay schedule → `config.yaml` / store-profiles JSON.
5. **Third-party portal automation uses `user-playwright` MCP**, never `cursor-ide-browser`.
6. **Cloud reads from cloud** — prod data from GCS / BQ / Secret Manager, not laptop files.
7. **Build & verify are part of the task** — run `scripts/verify.py --full` before declaring done.
8. **Rule files in `.cursor/rules/` MUST use the `.mdc` extension** — Cursor only loads `.mdc` as project rules; `.md` files are silently ignored. `AGENTS.md` is a special filename loaded regardless of extension. See `docs/contributing/rules.md` for authoring guidance.

---

## Three-tier guidance framework

| Tier | What | Where |
|---|---|---|
| **Tier 0 — Gates** | CI + `verify.py` (local mirror) | Mechanically enforced; can't be skipped |
| **Tier 1 — Spine** | This file + `CONTRIBUTING.md` + routing card + behavioral anchor + self-drive rule + new-requirement intake | ~150-200 lines; always-on |
| **Tier 2 — References** | `docs/WORKFLOW.md`, `docs/contributing/*`, glob-scoped agent specs, `RUNBOOK.md` | On-demand; loaded only when relevant |

---

## Documentation map

| If you want to… | Read |
|---|---|
| Operate / debug the live BHAGA cloud system | `RUNBOOK.md` |
| Verify/compare/screenshot a Grafana panel, or find the Grafana auth model | `agents/bhaga/grafana/README.md` |
| Understand BHAGA domain (orders, items, labor, tips, reviews) | `agents/bhaga/knowledge-base/DOMAIN.md` |
| Understand BHAGA pipeline code | `agents/bhaga/scripts/README.md` |
| Know BHAGA behavioral rules & invariants | `.cursor/rules/bhaga.mdc` |
| Open a PR / understand the review process | `CONTRIBUTING.md` + `docs/contributing/` |
| See the full lifecycle (phases, stages, tracking) | `docs/WORKFLOW.md` |
| Track work in flight / check phase state | `python3 scripts/phase_state.py report` |
| Run local CI mirror | `python3 scripts/verify.py --full` |
| See project state / decisions | `PROGRESS.md` |
| Missing a cloud secret locally (ClickUp, Google, Square, ADP, Slack) | `python3 -m skills.credentials.registry audit` (shows exact fix) then `hydrate <name>` |

---

## Keeping docs current

When you change behavior, update the doc in the same change:

| You changed… | Also update… |
|---|---|
| BHAGA pipeline behavior, OTP flow, sheets | `RUNBOOK.md` |
| BHAGA script, data flow, extending the model | `agents/bhaga/scripts/README.md` |
| BHAGA invariant / behavioral rule | `.cursor/rules/bhaga.mdc` |
| Lifecycle scripts (`phase_state.py`, `lifecycle.py`, `verify.py`, etc.) | `docs/WORKFLOW.md` |
| Cross-agent architecture, added agent/skill | `.cursor/rules/jarvis.mdc` + this file's tables |
| Added a new `.cursor/rules/*.mdc` file | Update `ALWAYS_ON` set in `verify_lifecycle.py::assert_16` if always-on; add row to Tier-1 table above |
| Added or changed a `/jarvis-*` Cursor Skill | `docs/contributing/skills.md` |
| Anything notable (status, decision, blocker) | `PROGRESS.md` (dated entry) |

Run `python3 scripts/check_doc_freshness.py` before finishing any change.
