# Cloud lifecycle ship tracker (Issue #228 / PR #234)

**Purpose:** Living ship gate for moving Jarvis development off the laptop filesystem onto Cloud Agents — no jam→ship disruptions.

**How to update:** edit this file in the same PR; bump `Last probed` when re-verified in a Cloud Agent VM.

| Field | Meaning |
|---|---|
| **Built** | Code/docs land on this branch (or already on `main`) |
| **Confidence** | Design/code readiness: HIGH / MED / LOW / NONE |
| **Verified (this cloud chat)** | Probed on Cloud Agent VM `bc-81438981…` (PR #234) — PASS / FAIL / PARTIAL / NOT RUN / N/A |
| **Ship?** | Must be `yes` or `waiver: …` before merge |

**Last probed:** 2026-08-10 ~01:05 UTC (this Cloud Agent).

**Operator goal (this PR):** close every non-waiver row before ship.

---

## Tracker

| ID | Capability | Built | Confidence | Verified (this cloud chat) | Evidence / notes | Ship? | Next action |
|---|---|---|---|---|---|---|---|
| C1 | `/jarvis-new-task` → Cloud Agent spawn (IDE + GH) | **yes** — `spawn_cloud_agent.py`, `new_requirement.py` cloud default, `jarvis-dev-signals` `intake-signal` | HIGH | **PARTIAL** | Helper imports; `CURSOR_AGENT_TOKEN` **MISSING** in this VM (GH Action has secret — cannot list secrets with `cursor` token). Fresh spawn from this chat **NOT RUN**. | no | Provision/confirm GH secret; dogfood one GH-comment intake OR run spawn with token; record agent URL |
| C2 | Tracking issue + remote branch (no mandatory worktree) | **yes** | HIGH | **PARTIAL** | Branch `fix/another-new-requirement-would-be-anytime` + Issue #228 + PR #234 exist. This VM was not created via a fresh intake dogfood in-probe. | no | One end-to-end intake from throwaway issue |
| C3 | `phase_state` init / advance from Cloud VM | **partial** — GH SoT exists; auto-hydrate on cloud boot **not built** | MED | **PARTIAL** | Manual `init --issue 228` → `Issue: #228` (was `#none`). `done` not restored from GH labels automatically. | no | **Build:** spawn/env start auto-hydrate; restore `done` from `approved:*` labels |
| C4 | Jam / operator gates (Plan-mode cloud) | **yes** (seed prompt + gate labels) | MED | **PARTIAL** | Labels present on #228. Ask-mode N/A on Cloud API. Live jam turn in this chat = operator chat, not re-probed. | waiver: gates already stamped | Document Plan-mode jam = cloud Ask substitute (docs only if missing) |
| C5 | Plan + `check_plan_readiness` | **yes** | HIGH | **NOT RUN** | Plan file exists; readiness script not re-run this probe. | no | `python3 scripts/check_plan_readiness.py docs/plans/i228-cloud-primary-intake.md` (+ expand plan if ship scope grows) |
| C6 | Implement + commit + push from Cloud Agent | **yes** (this PR’s commits) | HIGH | **PASS** | Labor Weekday fix + preview YAML fix committed/pushed from this VM; rebase onto `main` force-pushed. | yes | — |
| C7 | GitHub identity = `jarvis-agent-bot328` | **no** (env not wired) | LOW | **FAIL** | `gh auth` = **`cursor`** (integration), not bot. Cannot `gh issue comment` (403). | no | **Build:** Cursor env / spawn `GH_TOKEN` bot PAT; document; verify `gh api user` = bot |
| C8 | Secrets via `BHAGA_SECRETS_BACKEND=gcp` + ADC | **partial** — backend flag set; ADC missing | MED code / LOW live | **FAIL** | `BHAGA_SECRETS_BACKEND=gcp` set; `google.auth.default()` → DefaultCredentialsError; `registry audit` **0/29**. | no | **Ops+verify:** add ADC/SA JSON to Cloud Agent environment secrets; re-run `registry audit` → ≥1 PASS |
| C9 | `verify.py` (+ pytest) in Cloud VM | **partial** — `requirements-dev.txt` + env install (this PR) | MED | **PARTIAL** | After ad-hoc `pip install pytest`, `verify.py --fast` **PASS**. Clean-boot still needs new env build with `requirements-dev.txt`. `--full` NOT RUN. | no | Trigger env rebuild; re-probe `--fast` on clean boot; run `--full` |
| C10 | Git hooks + BQ cost capture on commit | **partial** — hooks in repo; not auto on cloud boot | LOW | **PARTIAL** | `core.hooksPath=scripts/git-hooks` after manual install. Pre-commit BQ capture **NOT RUN** (no ADC). Commits used `PR_COST_HOOK=0` / `VERIFY=0`. | no | Auto-run `install-git-hooks.sh` in `.cursor/environment.json`; ADC for BQ; one commit without bypass |
| C11 | Operator Console localhost poke | **N/A** (replaced) | N/A | **N/A** | By design: VM localhost not operator-reachable. | **waiver: use C12** | — |
| C12 | Tagged Cloud Run preview (IAP) | **yes** — `console_preview_deploy.py` + workflow | HIGH | **PASS** | Workflow green after YAML fix; `curl -I` → HTTP 302 IAP; Labor Weekday dogfood confirmed by operator. Preview: [pr234](https://pr234---operator-console-4yl5izovxq-uc.a.run.app) | yes | Post-merge `--remove-tags` |
| C13 | Labor dogfood (Weekday finished-only charts) | **yes** | HIGH | **PASS** | Unit tests PASS; operator visual OK. | yes | — |
| C14 | PR create `--base main` + description gates | **yes** (PR #234 open) | HIGH | **PASS** | `mergeable=MERGEABLE`; PR Base Branch + Description checks SUCCESS. | yes | — |
| C15 | PR cost ledger seed (`set-meta` / `record-build`) | **no** (not done this chat) | MED | **FAIL** | CI **PR cost gate FAILURE** (require-build). No ADC to write BQ from VM. | no | **Build/ops:** ADC or use CI WIF helper; `pr_cost_ledger.py set-meta --pr 234` + `record-build` / `capture-build` |
| C16 | Babysit loop (`pr_triage` + reply threads) | **partial** — scripts yes; auto-wake no | MED manual | **PARTIAL** | `pr_triage.py --pr 234` runs (saw Claude review + cost gate fails). No bot token to reply. Auto-wake **not built**. | no | Fix cost/Claude; reply as bot; then **Build C17** |
| C17 | Auto-wake Cloud Agent on CI fail / review comment | **no** | NONE | **NOT RUN** | Laptop listener/drain only. No `POST /v1/agents/{id}/runs` helper. | no | **Build next (core):** `followup_cloud_agent` + wire `jarvis-dev-signals` ci/comment jobs → resolve `bcId` → follow-up run; dogfood by failing a check |
| C18 | Auto-merge on operator approve | **yes** on `main` (`auto-merge-on-approval.yml`) | HIGH | **N/A** | Mac-independent; must not self-merge. | **waiver: GH-only; operator approves** | — |
| C19 | Post-merge verify + phase advance | **yes** on `main` (`pr-merged-lifecycle.yml`) | HIGH | **NOT RUN** | Needs a merge to exercise. | no (post-merge) | After merge: confirm lifecycle Action + issue advances |
| C20 | Retro auto-dispatch to Cloud Agent | **no** | NONE | **NOT RUN** | CI posts retro prompt; no cloud wake. | no | Extend C17 for `pr_merged` → retro seed prompt |
| C21 | Continue-anywhere handoff (Desktop/web URL) | **yes** — URL normalize + Desktop open helper | HIGH | **PARTIAL** | This agent URL works; Desktop `open` N/A on Linux VM. Issue comment handoff from spawn **not re-dogfooded** (no token). | no | Spawn dogfood must comment canonical `https://cursor.com/agents/bc-…` |
| C22 | Mac offline mid-flight (CI while laptop asleep) | **no** (blocked on C17) | NONE | **NOT RUN** | Without C17, signals sit on issue only. | no | Depends on C17 dogfood with Mac offline |

---

## Scoreboard (this probe)

| Status | Count | IDs |
|---|---|---|
| Ship? **yes** | 4 | C6, C12, C13, C14 |
| **waiver** | 3 | C4, C11, C18 |
| **no** (block ship) | 15 | C1–C3, C5, C7–C10, C15–C17, C19–C22 |

---

## Build order (do next, in this PR)

Operator asked: **this PR solves the full cloud loop before ship.** Prioritize by disruption:

### P0 — unblock this VM / this PR green
1. **C8 ADC** — Cursor environment secrets → GCP ADC/SA (ops). Re-probe `registry audit` + BQ.
2. **C15 cost ledger** — `set-meta` + record build for PR #234 (needs C8 or alternate WIF path).
3. **C9 pytest in env** — fix `.cursor/environment.json` / requirements so `verify.py --fast` passes.
4. **C7 bot `GH_TOKEN`** — so comments/replies/issue advances work as `jarvis-agent-bot328`.
5. **C3 phase hydrate** — `phase_state.py init --issue 228` (+ code so cloud boots don’t show `#none`).

### P1 — parity with laptop watcher (the “no disruption” goal)
6. **C17 auto-wake** — `scripts/spawn_cloud_agent.py` add `followup_run(bcId, prompt)`; `jarvis-dev-signals.yml` on `ci_failed` / review comment resolves `bcId` from issue comments → POST `/v1/agents/{id}/runs` (handle 409 busy).
7. **C16 babysit dogfood** — triage → fix → reply → green without Mac.
8. **C1/C2/C21 intake dogfood** — throwaway issue `/jarvis-new-task` from GH while documenting URL.

### P2 — close the loop
9. **C20 retro wake** — same follow-up path on `pr_merged`.
10. **C19** — verify on actual merge (or staging dry-run if exists).
11. **C22** — explicit Mac-offline dogfood checklist in §4.

### Explicit laptop-only waivers (do not build in this PR)
- Touch ID / passkeys / Keychain portals  
- launchd `com.jarvis.devsignals` (replaced by C17 for cloud-primary)  
- CHITRA `/tmp/jarvis-*` Socket Mode  
- Interactive localhost OAuth  
- Localhost console poke (C11 → C12)

---

## Verification commands (Cloud Agent)

```bash
python3 scripts/phase_state.py status
gh auth status                    # expect jarvis-agent-bot328
python3 -c 'import google.auth; print(google.auth.default()[1])'
python3 -m skills.credentials.registry audit | head
python3 scripts/verify.py --fast
python3 scripts/pr_triage.py --pr 234
curl -sI "https://pr234---operator-console-4yl5izovxq-uc.a.run.app/" | head -5
# after C17:
# python3 scripts/spawn_cloud_agent.py followup --agent bc-… --prompt "Run pr_triage and fix"
```

---

## Linkage

- Plan: [`docs/plans/i228-cloud-primary-intake.md`](i228-cloud-primary-intake.md)
- WORKFLOW §8: cloud intake + (today) local signal drain → target: cloud wake
- RUNBOOK §7: Cloud Agent bootstrap
- PR: https://github.com/aditya2kx/jarvis/pull/234
- Issue: https://github.com/aditya2kx/jarvis/issues/228
- This agent: https://cursor.com/agents/bc-81438981-33d9-4245-8405-e786cfb18a85
