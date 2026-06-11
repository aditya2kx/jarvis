#!/usr/bin/env bash
# Install the repo's git hooks (pre-commit: capture review cost into BQ).
# Points git at scripts/git-hooks via core.hooksPath — undo with:
#   git config --unset core.hooksPath
#
# pre-commit: captures review cost from PR comments into BigQuery (jarvis_dev)
#   on each commit. Build cost must be recorded explicitly by the author
#   (record-build or capture-build). The gate validates from BQ.
#
# Run this once on every fresh clone / worktree. See CONTRIBUTING.md § Cost ledger.
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
chmod +x scripts/git-hooks/* 2>/dev/null || true
git config core.hooksPath scripts/git-hooks
echo "installed: core.hooksPath=scripts/git-hooks"
echo "  pre-commit : capture-review -> BQ (build cost recorded separately)"
echo "uninstall: git config --unset core.hooksPath"
