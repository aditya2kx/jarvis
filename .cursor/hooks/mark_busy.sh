#!/usr/bin/env bash
# mark_busy.sh — beforeSubmitPrompt hook
# Writes state=busy + heartbeat to the worktree status lock.
# Observe-only: always returns {} so the prompt is never blocked.
set -euo pipefail

# Deps check (fail open if missing)
command -v git >/dev/null 2>&1 || { echo '{}'; exit 0; }
command -v python3 >/dev/null 2>&1 || { echo '{}'; exit 0; }

# Derive branch
BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
if [ -z "$BRANCH" ] || [ "$BRANCH" = "HEAD" ]; then
  echo '{}'
  exit 0
fi

# Derive slug (match phase_state._slug)
SLUG="$(python3 -c "import re,sys; print(re.sub(r'[^a-zA-Z0-9_-]','-','$BRANCH')[:60])" 2>/dev/null || true)"
if [ -z "$SLUG" ]; then
  echo '{}'
  exit 0
fi

# Write status lock
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
STATUS_FILE="$REPO_ROOT/metrics/pr_cost/session-${SLUG}-status.json"
mkdir -p "$(dirname "$STATUS_FILE")"

NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
python3 -c "
import json, os
p = '$STATUS_FILE'
try:
    data = json.loads(open(p).read()) if os.path.exists(p) else {}
except Exception:
    data = {}
data.update({'state': 'busy', 'heartbeat': '$NOW', 'turn_started_at': '$NOW'})
open(p, 'w').write(json.dumps(data))
" 2>/dev/null || true

echo '{}'
exit 0
