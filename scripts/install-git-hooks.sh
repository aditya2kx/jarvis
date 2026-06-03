#!/usr/bin/env bash
# Install the repo's opt-in git hooks (currently: pre-push cost-ledger sync).
# Points git at scripts/git-hooks via core.hooksPath — undo with:
#   git config --unset core.hooksPath
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
chmod +x scripts/git-hooks/* 2>/dev/null || true
git config core.hooksPath scripts/git-hooks
echo "installed: core.hooksPath=scripts/git-hooks (pre-push runs pr_cost_ledger.py sync)"
echo "uninstall: git config --unset core.hooksPath"
