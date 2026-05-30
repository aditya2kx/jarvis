---
description: Keep docs in lock-step with code (BHAGA/Jarvis). Auto-loads when editing code.
globs:
  - "agents/**"
  - "skills/**"
  - "cloud/**"
  - "core/**"
alwaysApply: false
---

# Doc maintenance — keep the repo self-sufficient

This repo is meant to be a **self-sufficient, cross-device source of truth**: any machine or cloud
agent should be able to clone it and keep working without re-discovering anything from a past chat.
That only holds if **docs move with the code**. This rule is the enforcement layer for that.

## The rule

**When you change code under the coupled paths below, update the matching doc in the *same* change.**
Not "later," not "in a follow-up" — same change. A doc that lags the code is a bug.

| You changed… | Update… |
|---|---|
| `agents/bhaga/scripts/*.py` or `skills/tip_ledger_writer/*.py` (pipeline, scripts, data model) | `agents/bhaga/scripts/README.md` (code map + "Extending the model") |
| sheet columns / metrics / domain meaning (`schema.py`, `update_model_sheet.py`, `forecast.py`, `process_reviews.py`) | `agents/bhaga/knowledge-base/DOMAIN.md` (data dictionary) |
| `cloud/**`, `.github/workflows/deploy.yml`, `daily_refresh*.py`, `otp_gate.py` (deploy / scheduler / secrets / OTP / orchestration) | `RUNBOOK.md` |
| `skills/tip_pool_allocation/*.py` or `store-profiles/*.json` (allocation invariant / sheet source of truth) | `.cursor/rules/bhaga.md` (+ `RUNBOOK.md` for sheets) |
| Anything notable (new capability, decision, blocker, status) | `PROGRESS.md` (dated line) |

The canonical map also lives in [`AGENTS.md`](../../AGENTS.md) § Keeping docs current.

## Before you finish a task

Run the deterministic checker on your change and resolve (or consciously dismiss) any reminder:

```bash
python3 scripts/check_doc_freshness.py            # uncommitted work
python3 scripts/check_doc_freshness.py --base origin/main   # a whole branch / push range
```

It's a **nudge, not a gate** — test-only changes and refactors won't always need a doc edit. Use
judgment, but default to writing it down: if a future you (or a cloud agent) would have to re-derive
it from a chat, it belongs in a doc.

## Keep the enforcement self-maintaining

When you add a **new** code↔doc dependency (a new doc, a new script that owns part of a doc, a new
coupled directory), **add a coupling to `COUPLINGS` in `scripts/check_doc_freshness.py`** and a row
to the table above. The checker should always reflect the current code↔doc graph — that's what makes
"docs stay relevant as we add more code" true by construction rather than by hope.
