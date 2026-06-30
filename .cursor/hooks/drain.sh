#!/usr/bin/env bash
# drain.sh — stop hook
# Fires when the agent finishes a turn (chat is idle).
# 1. Marks state=idle in the status lock.
# 2. If LOCAL_EVENT_AUTO_DISPATCH != 0 and pending events exist, pops the
#    oldest event from the FIFO inbox and returns:
#       {"followup_message": "<drain prompt>"}
#    so the agent continues in the same chat (non-preemptive warm drain).
# 3. Otherwise returns {} (no-op — unrelated chats are never auto-continued).
#
# SPIKE NOTE (M3 step 0):
#   The `stop` hook returning followup_message is documented via loop_limit but
#   not explicitly in the cheat-sheet. If this turns out to be unsupported,
#   drain.sh falls back to doing nothing here; the cold-start deeplink path in
#   dev_event_listener.dispatch() provides the one-click fallback.
set -euo pipefail

# Deps check (fail open)
command -v git >/dev/null 2>&1 || { echo '{}'; exit 0; }
command -v python3 >/dev/null 2>&1 || { echo '{}'; exit 0; }

BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
if [ -z "$BRANCH" ] || [ "$BRANCH" = "HEAD" ]; then
  echo '{}'
  exit 0
fi

SLUG="$(python3 -c "import re; print(re.sub(r'[^a-zA-Z0-9_-]','-','$BRANCH')[:60])" 2>/dev/null || true)"
if [ -z "$SLUG" ]; then
  echo '{}'
  exit 0
fi

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
STATUS_FILE="$REPO_ROOT/metrics/pr_cost/session-${SLUG}-status.json"
mkdir -p "$(dirname "$STATUS_FILE")"

# Mark idle
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
python3 -c "
import json, os
p = '$STATUS_FILE'
try:
    data = json.loads(open(p).read()) if os.path.exists(p) else {}
except Exception:
    data = {}
data.update({'state': 'idle', 'heartbeat': '$NOW'})
open(p, 'w').write(json.dumps(data))
" 2>/dev/null || true

# Check AUTO_DISPATCH
AUTO_DISPATCH="${LOCAL_EVENT_AUTO_DISPATCH:-1}"
if [ "$AUTO_DISPATCH" = "0" ] || [ "$AUTO_DISPATCH" = "false" ]; then
  echo '{}'
  exit 0
fi

# Drain oldest event from inbox
NEXT_EVENT="$(python3 "$REPO_ROOT/scripts/dev_event_router.py" drain --branch "$BRANCH" 2>/dev/null || true)"
if [ -z "$NEXT_EVENT" ]; then
  echo '{}'
  exit 0
fi

# Build the follow-up prompt based on event kind
KIND="$(echo "$NEXT_EVENT" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('kind','unknown'))" 2>/dev/null || echo "unknown")"
BRANCH_DISPLAY="$BRANCH"

case "$KIND" in
  babysit_ci)
    PROMPT="[AUTO-DISPATCH] CI failed on branch \`$BRANCH_DISPLAY\`. Read the babysit skill and fix all failing checks, then push once.\n\nEvent: $NEXT_EVENT"
    ;;
  retrospective)
    PROMPT="[AUTO-DISPATCH] PR merged for branch \`$BRANCH_DISPLAY\`. Perform the retrospective as per self-drive.mdc: read the PR conversation, grade the cycle, write a PROGRESS.md entry, harvest preferences. Then jam with the operator before closing the issue.\n\nEvent: $NEXT_EVENT"
    ;;
  ci_green)
    PROMPT="[AUTO-DISPATCH] CI passed on branch \`$BRANCH_DISPLAY\`. Check PR status and proceed with the next substep (babysit review replies, then wait for operator approval to merge).\n\nEvent: $NEXT_EVENT"
    ;;
  intake)
    PROMPT="[AUTO-DISPATCH] New requirement intake signal received.\n\nEvent: $NEXT_EVENT\n\nRun: python3 scripts/new_requirement.py --requirement \"<text from event>\""
    ;;
  *)
    PROMPT="[AUTO-DISPATCH] Event received for branch \`$BRANCH_DISPLAY\` (kind: $KIND).\n\nEvent: $NEXT_EVENT\n\nProcess this event appropriately."
    ;;
esac

# Return followup_message to continue in the same chat (spike-gated)
python3 -c "
import json
print(json.dumps({'followup_message': '$PROMPT'.replace(chr(10), '\\\\n')}))
" 2>/dev/null || echo '{}'
exit 0
