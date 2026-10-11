#!/usr/bin/env bash
# Cursor Cloud Agent VM: commit, push and use gh as jarvis-agent-bot328, exactly like the Mac
# (docs/contributing/push-gotchas.md §2). The bot PAT comes from Secret Manager `github-bot-pat`
# via Cursor OIDC (scripts/cloud_agent_gcp.py), or from a GH_TOKEN Cursor secret if set.
# Without either nothing changes. Idempotent — safe on every VM start.
set -euo pipefail

BOT=jarvis-agent-bot328
BOT_EMAIL=aditya.2ky+jarvisbot@gmail.com
here="$(cd "$(dirname "$0")" && pwd)"

[ "$(uname -s)" = Darwin ] && exit 0
token="${GH_TOKEN:-}"
[ -n "$token" ] || token="$(python3 "$here/cloud_agent_gcp.py" secret github-bot-pat)" || token=""
if [ -z "$token" ]; then
  echo "cloud-git-identity: no bot PAT (Secret Manager github-bot-pat via Cursor OIDC, or GH_TOKEN) — commits/pushes stay as the Cursor app"
  exit 0
fi
login="$(GH_TOKEN="$token" gh api user --jq .login 2>/dev/null)" || login=""
if [ "$login" != "$BOT" ]; then
  echo "cloud-git-identity: token is '${login:-invalid}', not $BOT — git identity unchanged"
  exit 0
fi

# Agent shells start after this script, so persist the token in gh's own config rather than env.
printf '%s' "$token" | env -u GH_TOKEN gh auth login --hostname github.com --with-token
# Cursor injects its GitHub App token via url.<token-url>.insteadOf in ~/.gitconfig and in the
# remote URL; either would win over gh's credential helper and push as the `cursor` app.
git config --global --name-only --get-regexp '^url\..*@github\.com/\.insteadof$' 2>/dev/null \
  | sed 's/\.insteadof$//' | sort -u \
  | while read -r section; do git config --global --remove-section "$section"; done
repo="$(git -C "$here" rev-parse --show-toplevel 2>/dev/null || true)"
if [ -n "$repo" ]; then
  git -C "$repo" remote set-url origin https://github.com/aditya2kx/jarvis
  git -C "$repo" config --unset-all user.name || true
  git -C "$repo" config --unset-all user.email || true
fi
env -u GH_TOKEN gh auth setup-git
git config --global user.name "$BOT"
git config --global user.email "$BOT_EMAIL"
echo "cloud-git-identity: commits, pushes and gh as $BOT"
