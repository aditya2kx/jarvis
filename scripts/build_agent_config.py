#!/usr/bin/env python3
"""Build the AGENT_CONFIG_JSON env var for the bhaga-webhook Cloud Run service.

The webhook (cloud/webhook/handler.py) is a standalone deploy unit that cannot
import the `core`/`skills` packages, so it learns which DM channel belongs to
which agent purely from the AGENT_CONFIG_JSON env var. config.yaml's
`slack.agents` section is the single source of truth for those mappings; this
script projects it down to the routing-only keys (NO secrets / bot_token_cmd)
and prints the JSON.

Usage (inject onto the live service — deploy.yml only updates the image, so the
env var persists across image redeploys):

    gcloud run services update bhaga-webhook \
        --region us-central1 --project jarvis-bhaga-prod \
        --update-env-vars AGENT_CONFIG_JSON="$(python3 scripts/build_agent_config.py)"

Each emitted agent entry carries any of: dm_channel, cloud_dm_channel,
dm_channels. handler._init_agent_config maps every one of them to the agent.
"""

from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Only these keys affect webhook routing. Everything else in slack.agents
# (bot_token_cmd, identity_mode, display_prefix, ...) is intentionally dropped
# so no credentials leak into the Cloud Run env.
_ROUTING_KEYS = ("dm_channel", "cloud_dm_channel", "dm_channels")


def build_agent_config() -> dict:
    from core.config_loader import load_config

    cfg = load_config()
    agents = (cfg.get("slack", {}) or {}).get("agents", {}) or {}

    out: dict[str, dict] = {}
    for name, agent_cfg in agents.items():
        if not isinstance(agent_cfg, dict):
            continue
        entry = {k: agent_cfg[k] for k in _ROUTING_KEYS if agent_cfg.get(k)}
        if entry:
            out[name] = entry
    return out


def main() -> int:
    print(json.dumps(build_agent_config(), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
