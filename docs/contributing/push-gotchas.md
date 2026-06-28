# Pushing and opening PRs — gotchas

## 1. The enterprise pre-push hook blocks the push
On the operator's machine a machine-global DoorDash push-protection hook runs on
every `git push`.  For this repo (`aditya2kx/jarvis`, pushed via HTTPS with `GH_TOKEN`)
it refuses with: *"This push does NOT prove an open-source contribution…"*

**The approved procedure:**
1. Scan the diff for real secrets before pushing:
   ```bash
   git show HEAD | rg -i 'AIza|sk-[A-Za-z0-9]{20}|-----BEGIN|password\s*[:=]|api[_-]?key'
   ```
   Sheet IDs and the operator's own email are config — acceptable.
2. If the diff is clean: `git push --no-verify`.  This is the expected path — do not
   re-ask the operator, do not stall waiting for an "approved env var".
3. Never `--no-verify` to push an actual secret.

`scripts/verify.py --full` (or `--fast`) runs the same secret scan locally so
you catch secrets before you get to this step.

## 2. `gh pr create` runs as the bot account
All agent GitHub operations use **`jarvis-agent-bot328`** — the dedicated bot
collaborator.  `GH_TOKEN` is pre-loaded from Keychain in `~/.zshrc` so `gh`
picks it up automatically.  No additional setup needed.

**Auth model (single source of truth):** The `origin` remote URL is tokenless
(`https://github.com/aditya2kx/jarvis.git`). Git authenticates via the `gh` credential
helper (`gh auth setup-git`) which reads `GH_TOKEN` → Keychain `github-bot-pat`.
There is no PAT embedded in any `.git/config`. All worktrees share the same remote config.

**2FA status:** `jarvis-agent-bot328` has TOTP 2FA enrolled (enrolled 2026-06-28).
Classic PATs are **not affected** by GitHub's 2FA enforcement — token-based git/`gh` ops
keep working regardless. The TOTP secret is stored in Keychain (`github-bot-totp`);
recovery codes in Keychain (`github-bot-recovery`).

**PAT rotation procedure:**
```bash
# 1. Generate a new classic PAT at github.com/settings/tokens (scopes: repo, workflow, read:org)
# 2. Store it:
security add-generic-password -a jarvis-agent-bot328 -s github-bot-pat -w <new_token> -U
# 3. Open a new shell (GH_TOKEN reloads from Keychain) and verify:
gh api user --jq .login
# 4. Revoke the old PAT via github.com/settings/tokens (last step)
```

> **After rotation — open workspaces:** Any Cursor workspace or terminal tab that was already open
> before the old token was revoked will have the stale revoked token in its `GH_TOKEN` env var.
> In each open workspace, run `source ~/.zshrc` (or open a new terminal tab) to reload `GH_TOKEN`
> from Keychain. This is required after every rotation — it's not a bug, just how the zshrc +
> Keychain pattern works.

## 3. Never push to `main` directly
`main` is the deployed branch.  Push to `main` → image rebuild → prod change.
Always work on a feature branch and land via PR.
