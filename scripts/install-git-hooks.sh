#!/usr/bin/env bash
# Install the repo's git hooks + one-time Cursor hook dispatcher.
#
# Git hooks (points git at scripts/git-hooks via core.hooksPath):
#   pre-commit : captures review cost from PR comments into BigQuery (jarvis_dev)
#                on each commit.  Build cost must be recorded explicitly by the
#                author (record-build or capture-build).  The gate validates from BQ.
#                Set PR_COST_HOOK=0 to skip a WIP commit.
#   pre-push   : runs scripts/verify.py --full — the full local CI mirror — before
#                any push.  Catches secret-scan, test, doc-freshness, PR-desc, and
#                review-replies failures before they hit CI.
#                Set VERIFY=0 to bypass (only after confirming the diff is clean).
#
# Cursor hook dispatcher (one-time per laptop, not per worktree):
#   ~/.cursor/hooks.json  beforeSubmitPrompt dispatcher that executes
#   $CURSOR_PROJECT_DIR/.cursor/hooks/enforce.sh on every user prompt.
#   The enforcement script is repo-versioned and travels with each branch.
#   No-ops gracefully when the repo doesn't have .cursor/hooks/enforce.sh.
#   Idempotent — merges with any existing ~/.cursor/hooks.json entries.
#
# Run this once on every fresh clone / laptop.
# See docs/contributing/local-loop.md, docs/contributing/cost.md,
# and docs/contributing/hooks.md for the Cursor hook setup.
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
chmod +x scripts/git-hooks/* 2>/dev/null || true
chmod +x .cursor/hooks/enforce.sh 2>/dev/null || true
git config core.hooksPath scripts/git-hooks

# Seed GitHub Issue labels for phase tracking (idempotent; requires gh auth).
if command -v gh >/dev/null 2>&1 && python3 -c "import scripts.phase_state" 2>/dev/null; then
    python3 scripts/phase_state.py ensure-labels 2>/dev/null || true
fi

echo "installed: core.hooksPath=scripts/git-hooks"
echo "  pre-commit : capture-review -> BQ  (bypass: PR_COST_HOOK=0)"
echo "  pre-push   : verify.py --full      (bypass: VERIFY=0)"
echo "uninstall: git config --unset core.hooksPath"

# ---------------------------------------------------------------------------
# One-time Cursor hook dispatcher — idempotent merge into ~/.cursor/hooks.json
# ---------------------------------------------------------------------------
_install_cursor_dispatcher() {
    local hooks_file="$HOME/.cursor/hooks.json"
    mkdir -p "$(dirname "$hooks_file")"

    python3 - "$hooks_file" <<'PYEOF'
import json, os, sys

hooks_file = sys.argv[1]
cmd = (
    "bash -c "
    "'f=\"$CURSOR_PROJECT_DIR/.cursor/hooks/enforce.sh\"; "
    "[ -x \"$f\" ] && exec \"$f\" || echo \\'{ \"continue\": true }\\' '"
)

entry = {"command": cmd, "failClosed": False}

if not os.path.exists(hooks_file):
    doc = {"version": 1, "hooks": {"beforeSubmitPrompt": [entry]}}
    with open(hooks_file, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    print(f"created: {hooks_file}")
else:
    try:
        with open(hooks_file) as f:
            doc = json.load(f)
    except Exception as e:
        print(f"warning: could not parse {hooks_file}: {e}", file=sys.stderr)
        sys.exit(0)
    doc.setdefault("hooks", {})
    entries = doc["hooks"].setdefault("beforeSubmitPrompt", [])
    if any(e.get("command") == cmd for e in entries):
        print("cursor hook dispatcher: already present — skipped")
        sys.exit(0)
    entries.append(entry)
    with open(hooks_file, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    print(f"merged cursor hook dispatcher into {hooks_file}")
PYEOF
}

_install_cursor_dispatcher
echo "  cursor hook : beforeSubmitPrompt dispatcher -> ~/.cursor/hooks.json"
echo "                enforcement script: .cursor/hooks/enforce.sh (repo-versioned)"
echo "                bypass: prefix prompt with  //inline"
