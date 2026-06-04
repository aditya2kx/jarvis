#!/usr/bin/env bash
# Install the repo's git hooks (currently: pre-commit cost-ledger sync).
# Points git at scripts/git-hooks via core.hooksPath — undo with:
#   git config --unset core.hooksPath
#
# The pre-commit hook folds the per-PR cost ledger (metrics/pr_cost/) into your
# own commits so it lands on main in the squash merge — no bot commit, no CI
# push-back. Run this once on every fresh clone / worktree (including cloud
# agents). See CONTRIBUTING.md § Cost ledger.
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
chmod +x scripts/git-hooks/* 2>/dev/null || true
git config core.hooksPath scripts/git-hooks
echo "installed: core.hooksPath=scripts/git-hooks (pre-commit runs capture-review + report)"
echo "uninstall: git config --unset core.hooksPath"
