# Requirements — Adi's Running List

> **This is the live progress tracker.** Status is auto-updated by `start_pr_session.py` (→ In Progress) and `pr-cost-finalize.yml` (→ Done) when requirements are linked via `--requirement-id`.
>
> To add a new requirement, append it below and commit. To link a PR to a requirement, pass `--requirement-id <N>` to `start_pr_session.py`.

| Status | ID | Requirement | PR(s) | Notes |
|--------|----|-------------|-------|-------|
| ✅ Done | 3 | Auto-halt + auto-resume on known-bad runs | #12 | Semantic guards + pipeline-halt circuit breaker. Keep open for failure classes not yet guarded. |
| ✅ Done | 7 | Cost / token monitoring per PR | #13, #14, #15, #17 | `pr_cost_ledger.py` + HTML report + auto-capture + post-merge finalize. |
| ✅ Done | 9 | ADP earnings report unavailable / empty (cadence edge case) | #12 | Handled gracefully; cadence-safe. Watch for self-resolution of 5/18–5/31. |
| ✅ Done | 12b | Post-merge CI cost finalize (`pr-cost-finalize.yml`) | #17 | Auto-captures review cost + regenerates report at merge. Build half is local-only. |
| ✅ Done | 18a | BQ parallel store + Grafana Cloud analytics dashboard | #18 | Dual-write BQ + Sheets; Grafana dashboard deployed. |
| ✅ Done | 19 | Bot GitHub identity (`jarvis-agent-bot328`) + PR enforcement | #19 | Bot account, branch protection, PR description CI gate, Claude review verdict gate. |
| 🔄 In Progress | 21 | Auto-commit cost report to main on merge (ADMIN_PAT bypass) | #21 | `pr-cost-finalize.yml` was silently dropping the report commit. |
| 🔲 Pending | 1 | Developer / onboarding guide | — | Setup + onboarding for anyone working with Jarvis. Ref: dee862f9, 210a13a8. |
| 🔲 Pending | 2 | Fix 5/31 root causes + observability gaps | — | Confirm all 5/31 root causes fully resolved. |
| 🔲 Pending | 4 | Ask + code against BHAGA from Slack | — | Ability to ask questions and code from Slack against BHAGA. |
| 🔲 Pending | 5 | Zero-shift guard | — | ADP shifts not lining up with sales → halt and ask. |
| 🔲 Pending | 6 | Smarter sandbox suite selection on PR CI | — | Skip full e2e for tooling/docs-only PRs. Plan written; needs implementation. |
| 🔲 Pending | 8 | Feature-build automation ("PR babysitter") | — | Automated morning PR review + comment addressing loop. |
| 🔲 Pending | 10 | Smart scraping (skip already-scraped sources) | — | Ignore Square/ADP/Google Reviews if already scraped for the day. |
| 🔲 Pending | 11 | Cost framework v2 — speed, reliability, file-level drill-down | — | Speed (wall-clock), reliability (rounds-to-green, CI history, churn), file/phase drill-down. |
| 🔲 Pending | 12a | One-click local finalize-and-merge | — | `finalize-merge --pr N` captures build + review + commits + merges in one command. |
| 🔲 Pending | 13 | Square magic link asked every time (trust device broken) | — | Magic link prompt on every Square prod login; "trust this device" not persisting. |
| 🔲 Pending | 14 | BQ missing 6/2 data from backfill | — | BigQuery backfill did not include June 2 data. Verify and re-backfill. |
| 🔴 P0 | 15 | BQ incremental run failure | — | Incremental run to BQ failed. Root cause TBD — investigate and fix immediately. |

---

## Archive / Detail Notes

### #6 — Smarter sandbox suite selection
```
PROBLEM: .github/workflows/sandbox-e2e.yml triggers on every pull_request with NO path filter.
Pure tooling/docs PRs (e.g. #13) run a full prod-raw replay for nothing.
FIX: add early changed-paths check in the single 'Sandbox e2e' job:
  - pipeline-relevant paths changed → full replay
  - else → post 'not applicable' evidence + exit 0 (always green)
POLICY = CONSERVATIVE: skip ONLY when EVERY changed file is inert
  (top-level scripts/**, metrics/**, **/*.md). Unknown/new path → run full e2e.
LOCK-STEP: update CONTRIBUTING §4 wording when implementing.
```

### #7 — Cost monitoring (Done)
```
v1: pr_cost_ledger.py + PR-<n>.json data source.
BUILD-CAPTURE: cursor_usage.py reads local Cursor session token → exact per-request
  tokenUsage + chargedCents + model. capture-build --pr N auto-windows from
  ~/.cursor/ai-tracking/ai-code-tracking.db (edit-anchored, capped at merge).
REVIEW-CAPTURE: post_claude_review_cost.py posts cost comment per CI run;
  capture-review rebuilds rows from those comments.
HTML REPORT: pr_cost_ledger.py report → metrics/pr_cost/report.html.
```

### #11 — Cost framework v2 gaps
```
(a) SPEED    — wall-clock duration per session + per review run. $/min + slowest phases.
(b) RELIABILITY — rounds-to-green, CI pass/fail history, churn (lines added then deleted).
(c) FILE DRILL-DOWN — ai-code-tracking.db maps requestId→files; join to usage events.
DELIVERABLE: new columns in PR-<n>.json + richer analyze output.
```

### #12a — Local finalize-and-merge (still TODO)
```
HARD CONSTRAINT: build tokens are LOCAL-ONLY (Cursor session token + ai-code-tracking.db).
DESIGN: pr_cost_ledger.py finalize-merge --pr N
  → capture-build (auto-window) + capture-review + validate --require-build
  → commit metrics/pr_cost/PR-N.json → push → wait for checks green → gh pr merge
```

### #13 — Square magic link / trust device
```
Magic link is prompted on every Square prod login.
"Trust this device" setting is not persisting across sessions.
Likely cause: browser profile / cookie not being reused across Playwright sessions.
```

### #14 — BQ missing 6/2 data
```
BigQuery does not have June 2 data from the backfill run.
Need to investigate: did the backfill job skip 6/2? Did it fail silently?
Re-run targeted backfill for 2026-06-02.
```

### #15 — BQ incremental run failure [P0]
```
The incremental run to BQ failed.
Root cause unknown — needs immediate investigation.
Check: Cloud Run logs, daily_refresh.py error output, BQ write errors.
```
