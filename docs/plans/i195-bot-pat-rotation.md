# Rotate expiring jarvis-agent-bot328 classic PAT (unify custody)

Evidence tier: unit-only
waiver: ops+docs-only; no BHAGA pipeline/runtime; sandbox-live/e2e N/A (same class as PR #106 / #103)

## Jam / §4 (approved 2026-07-26)

- **Scope B:** one new classic PAT (`repo`, `workflow`, `read:org`, no expiry) written to both bot Keychain slots (`github-bot-pat` + `github_pat` acct `jarvis-agent-bot328`) **and** repo secret `ADMIN_PAT`; revoke both old bot tokens after verify.
- Agent self-drives via Playwright + Keychain TOTP (prefs #20–22).
- Feature flag: none — ops/docs cannot silently produce wrong tip numbers.
- Model routing: Sonnet for implement/docs; Opus only if Playwright login hard-fails.

### Per-scenario evidence (PR §4)

1. **Happy path — laptop:** fresh `GH_TOKEN` from Keychain → `gh api user --jq .login` = `jarvis-agent-bot328`; `X-Oauth-Scopes` includes `read:org, repo, workflow`; `git ls-remote origin HEAD` exit 0.
2. **Happy path — custody unify:** sha256 fingerprints of Keychain `github-bot-pat` and `github_pat` (bot) match; neither equals owner `github-pat`.
3. **Happy path — CI:** workflow using `ADMIN_PAT` posts/acts as `jarvis-agent-bot328` (e.g. `jarvis-dev-signals` comment author).
4. **Failure/recovery — stale shell:** after revoke, old shell `GH_TOKEN` → 401; `source ~/.zshrc` / new tab recovers (documented).
5. **Legacy — old tokens dead:** Bearer curl of each revoked old token → HTTP 401.
6. Screenshots on https release URLs (pref #18): new PAT created; tokens page shows only new bot PAT.

## Citations

- `docs/contributing/push-gotchas.md` lines 21–53 (`PAT rotation procedure` — incomplete today: Keychain-only)
- `RUNBOOK.md` lines 462–463 (laptop checklist bot-PAT rotation — Keychain-only)
- `.github/workflows/jarvis-dev-signals.yml` line 72 (`GH_TOKEN: ${{ secrets.ADMIN_PAT }}`)
- `.github/workflows/auto-merge-on-approval.yml` line 32; `pr-merged-lifecycle.yml` line 36
- Prior: PR #106 / Issue #103 rotation evidence pattern; commit `328d4e3` (ADMIN_PAT → bot PAT)
- Prefs: `user-preferences.mdc` #18, #20, #21, #22
- Docs lock-step: `push-gotchas.md` + `RUNBOOK.md`; `python3 scripts/check_doc_freshness.py`
- Branch: `fix/i195-your-personal-access-token-classic-is`; bot `jarvis-agent-bot328`; never self-merge; `--base main`

## Concrete commands / artifacts

```bash
# After minting NEW_PAT (never commit the value):
security add-generic-password -a jarvis-agent-bot328 -s github-bot-pat -w "$NEW_PAT" -U
security add-generic-password -a jarvis-agent-bot328 -s github_pat -w "$NEW_PAT" -U
gh secret set ADMIN_PAT --repo aditya2kx/jarvis --body "$NEW_PAT"

# Verify (fresh env):
export GH_TOKEN="$(security find-generic-password -s github-bot-pat -a jarvis-agent-bot328 -w)"
gh api user --jq .login   # jarvis-agent-bot328
gh api user -i 2>&1 | rg -i 'x-oauth-scopes'
git ls-remote origin HEAD

# Fingerprint unify (no full secret print):
python3 - <<'PY'
import hashlib, subprocess
def w(s):
    return subprocess.check_output(["security","find-generic-password","-s",s,"-a","jarvis-agent-bot328","-w"], text=True).strip()
a,b = w("github-bot-pat"), w("github_pat")
print(hashlib.sha256(a.encode()).hexdigest()[:12], a==b)
PY

# Old token dead:
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $OLD_PAT" https://api.github.com/user  # 401

python3 scripts/verify.py --full
python3 scripts/check_doc_freshness.py
```

Doc edit target — replace rotation block in `push-gotchas.md` §2 with dual-Keychain + `gh secret set ADMIN_PAT` + verify-before-revoke ordering; mirror one-liner in `RUNBOOK.md` L463.

## Invariants

- Never commit `ghp_` values; secret-scan must pass.
- Old tokens stay valid until new path proven (laptop + ADMIN_PAT consumer), then revoke last.
- Tokenless `origin` URL preserved (`https://github.com/aditya2kx/jarvis.git`).
- Owner PAT (`github-pat` / `aditya2kx`) untouched.
- Integer-cents / tip math / sandbox isolation: N/A (no pipeline code).

## Milestone 1 — Rotate + unify custody (Sonnet)

Playwright as `jarvis-agent-bot328` (TOTP from Keychain `github-bot-totp`): create classic PAT note `jarvis-admin-pat-2026-07` (or regenerate), scopes `repo` `workflow` `read:org`, no expiry. Write both Keychain slots + `ADMIN_PAT`. Verify laptop identity/scopes/`git ls-remote`. Confirm ADMIN_PAT via bot-authored Actions side effect. Revoke both old bot PATs. Capture screenshots → `evidence-screenshots` release.

**Verify:** checklist items 1–5 above; fingerprints match.

## Milestone 2 — Docs (Composer/Sonnet)

Update `docs/contributing/push-gotchas.md` §2 rotation procedure + `RUNBOOK.md` L462–463; optional PROGRESS dated line lands post-merge via retro, not required pre-merge if gate allows.

**Verify:**
```bash
python3 scripts/check_doc_freshness.py
python3 scripts/verify.py --full
```

## Milestone 3 — PR §4 assembly + babysit (Sonnet)

Open PR `--base main`, `Closes #195`, bind cost ledger, paste §4 evidence, babysit to green. Hand merge to operator.

**Verify:** `python3 scripts/pr_triage.py --pr N` clean; CI green.
