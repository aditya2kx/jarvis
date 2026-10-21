#!/usr/bin/env bash
# Pin this clone to jarvis-agent-bot328 for commits and pushes on every surface (Mac, Cursor Cloud
# Agent, remote host). Repo-local config, so it outranks whatever Cursor rewrites globally mid-session:
#   - user.name / user.email     -> the bot (commit author)
#   - remote.origin.pushurl      -> https://jarvis-agent-bot328@github.com/... ; a global
#                                   `url.<token-url>.insteadOf https://github.com/` no longer matches,
#                                   and a token Cursor writes into remote.origin.url is not used to push
#   - credential helper (reset)  -> scripts/git-credential-jarvis-bot, which asks gh for the bot's
#                                   token by name instead of gh's active account
# Idempotent. Called by scripts/install-git-hooks.sh and scripts/cloud_git_identity.sh.
set -euo pipefail

BOT=jarvis-agent-bot328
BOT_EMAIL=aditya.2ky+jarvisbot@gmail.com
REPO_PATH=aditya2kx/jarvis

repo="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
cfg() { git -C "$repo" config "$@"; }

cfg user.name "$BOT"
cfg user.email "$BOT_EMAIL"
cfg remote.origin.pushurl "https://$BOT@github.com/$REPO_PATH"
cfg --unset-all credential.https://github.com.helper 2>/dev/null || true
cfg --add credential.https://github.com.helper ""
# Resolved at run time so every worktree uses its own copy of the helper.
cfg --add credential.https://github.com.helper '!"$(git rev-parse --show-toplevel)/scripts/git-credential-jarvis-bot"'
echo "pin-git-identity: commits and pushes as $BOT (repo-local)"
