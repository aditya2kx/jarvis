#!/usr/bin/env bash
# Cursor Cloud Agent VM: commit and push as jarvis-agent-bot328, exactly like the Mac
# (docs/contributing/push-gotchas.md §2). Needs the bot classic PAT as the Cursor secret
# GH_TOKEN; without it nothing changes. Idempotent — safe on every VM start.
set -euo pipefail

BOT=jarvis-agent-bot328
BOT_EMAIL=aditya.2ky+jarvisbot@gmail.com

[ "$(uname -s)" = Darwin ] && exit 0
if [ -z "${GH_TOKEN:-}" ]; then
  echo "cloud-git-identity: GH_TOKEN not set — commits/pushes stay as the Cursor app (dev-anywhere.md § Operator one-time setup)"
  exit 0
fi
login="$(gh api user --jq .login 2>/dev/null)" || login=""
if [ "$login" != "$BOT" ]; then
  echo "cloud-git-identity: GH_TOKEN is '${login:-invalid}', not $BOT — git identity unchanged"
  exit 0
fi

# Cursor injects its GitHub App token via url.<token-url>.insteadOf in ~/.gitconfig and in the
# remote URL; either would win over gh's credential helper and push as the `cursor` app.
git config --global --name-only --get-regexp '^url\..*@github\.com/\.insteadof$' 2>/dev/null \
  | sed 's/\.insteadof$//' | sort -u \
  | while read -r section; do git config --global --remove-section "$section"; done
repo="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -n "$repo" ]; then
  git -C "$repo" remote set-url origin https://github.com/aditya2kx/jarvis
  git -C "$repo" config --unset-all user.name || true
  git -C "$repo" config --unset-all user.email || true
fi
gh auth setup-git
git config --global user.name "$BOT"
git config --global user.email "$BOT_EMAIL"
echo "cloud-git-identity: commits and pushes as $BOT"
