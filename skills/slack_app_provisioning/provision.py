#!/usr/bin/env python3
"""Slack app provisioning — manifest generator + Playwright playbook builder.

This module produces (a) the YAML manifest to paste at api.slack.com/apps/new
and (b) a structured plan of browser actions the AI agent executes against the
`user-playwright` MCP. After Playwright captures the bot + app tokens, the
caller hands them to `register.register_agent_identity()` to finalize.

Why a "playbook" rather than executing Playwright directly:
    Playwright is invoked through the MCP layer by the AI agent itself, not by
    a Python subprocess. The skill cannot call `browser_navigate` from Python.
    What it CAN do is: produce a deterministic, reviewable, reusable sequence
    of MCP-tool calls + the structured arguments + the post-conditions to
    check at each step. The AI executes them.

Mirrors the same pattern as `skills/browser/collaborative.generate_login_plan()`.
"""

from __future__ import annotations

import os
import pathlib
import sys
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from core.config_loader import project_dir


_SKILL_DIR = pathlib.Path(__file__).resolve().parent
DEFAULT_MANIFEST_PATH = _SKILL_DIR / "default_manifest.yaml"


def render_manifest(
    agent_name: str,
    description: str,
    template_path: Optional[pathlib.Path] = None,
) -> str:
    """Substitute agent identity into the manifest template.

    Args:
        agent_name: e.g. "BHAGA" — used as both display name and bot username
        description: Short tagline shown in Slack's app directory
        template_path: Override default template (per-agent customization)

    Returns:
        The rendered YAML as a string, ready to paste into Slack's "From a
        manifest" textarea.
    """
    src = template_path or DEFAULT_MANIFEST_PATH
    raw = src.read_text()
    return raw.replace("__AGENT_NAME__", agent_name).replace(
        "__AGENT_DESCRIPTION__", description
    )


def resolve_agent_manifest(agent_name: str, description: str) -> tuple[str, pathlib.Path]:
    """Resolve the manifest for an agent, preferring per-agent override.

    Lookup order:
        1. agents/<name>/setup/slack-app-manifest.yaml   (per-agent, full file)
        2. skills/slack_app_provisioning/default_manifest.yaml + render

    Returns:
        (rendered_yaml_str, source_path_used)
    """
    per_agent = (
        pathlib.Path(project_dir())
        / "agents"
        / agent_name.lower()
        / "setup"
        / "slack-app-manifest.yaml"
    )
    if per_agent.exists():
        return per_agent.read_text(), per_agent
    return render_manifest(agent_name, description), DEFAULT_MANIFEST_PATH


def build_plan(
    agent_name: str,
    workspace_slug: str,
    description: str = "",
    manifest_path: Optional[str] = None,
) -> dict:
    """Produce a structured plan for the AI to drive against `user-playwright`.

    Each step has:
        - id: stable identifier
        - action: which MCP tool to call ("browser_navigate", "browser_snapshot",
          "browser_click", "browser_type", "browser_evaluate", "browser_wait_for",
          "collaborative_handoff")
        - description: human-readable intent (also fine for Slack updates)
        - args: kwargs to pass to the MCP tool
        - postcondition: what the AI must verify before moving on
        - capture: what the AI must extract from the result and stash for later
        - on_failure: what to do if postcondition isn't met

    The AI is responsible for snapshotting between steps and adapting selectors
    if Slack's UI shifts. Selectors here are HINTS, not contracts.
    """
    manifest_yaml, manifest_source = resolve_agent_manifest(agent_name, description)

    return {
        "agent_name": agent_name,
        "agent_lower": agent_name.lower(),
        "workspace_slug": workspace_slug,
        "manifest_yaml": manifest_yaml,
        "manifest_source": str(manifest_source),
        "captures": {
            # The AI fills these as it goes; register.py consumes the final dict.
            "bot_token": None,
            "app_token": None,
            "app_id": None,
        },
        "steps": [
            {
                "id": "open_apps_index",
                "action": "browser_navigate",
                "description": "Open Slack 'New App' page",
                "args": {"url": "https://api.slack.com/apps?new_app=1"},
                "postcondition": "URL contains api.slack.com/apps and login is not required",
                "on_failure": {
                    "if": "redirected to slack.com/signin or login form visible",
                    "do": "collaborative_handoff",
                    "reason": "Slack admin session expired — user must log in once",
                },
            },
            {
                "id": "click_from_manifest",
                "action": "browser_snapshot_then_click",
                "description": "Choose 'From a manifest' option in the new-app modal",
                "selectors_hint": [
                    "button:has-text('From a manifest')",
                    "[data-qa='from_manifest']",
                    "div.p-create_app_modal__radio_label:has-text('From a manifest')",
                ],
                "postcondition": "Workspace selector dropdown is visible",
            },
            {
                "id": "select_workspace",
                "action": "browser_snapshot_then_select",
                "description": f"Pick workspace '{workspace_slug}' in the dropdown",
                "selectors_hint": [
                    "[data-qa='workspace_picker']",
                    "div.c-select_input",
                ],
                "value": workspace_slug,
                "postcondition": "Selected workspace matches workspace_slug",
            },
            {
                "id": "advance_to_yaml",
                "action": "browser_click",
                "description": "Click 'Next' to advance to manifest paste step",
                "selectors_hint": [
                    "button:has-text('Next')",
                    "[data-qa='dialog_go_button']",
                ],
                "postcondition": "Manifest YAML/JSON textarea is visible",
            },
            {
                "id": "paste_manifest",
                "action": "browser_type",
                "description": "Paste the rendered manifest YAML",
                "selectors_hint": [
                    "textarea[name='manifest']",
                    "div.ace_editor textarea",
                    "[contenteditable='true']",
                ],
                "value_ref": "manifest_yaml",
                "postcondition": "Textarea contains the manifest body",
            },
            {
                "id": "create_app",
                "action": "browser_click",
                "description": "Click Next then Create",
                "selectors_hint": ["button:has-text('Next')", "button:has-text('Create')"],
                "postcondition": "Landed on the new app's settings page (URL contains /apps/A...)",
                "capture": {"app_id": "regex:/apps/(A[A-Z0-9]+)"},
            },
            {
                "id": "install_to_workspace",
                "action": "browser_click",
                "description": "Click 'Install to Workspace' on Basic Information page",
                "selectors_hint": [
                    "button:has-text('Install to')",
                    "[data-qa='install_app_button']",
                ],
                "postcondition": "Slack OAuth approval page is visible",
            },
            {
                "id": "approve_oauth",
                "action": "browser_click",
                "description": "Approve the OAuth scope grant",
                "selectors_hint": [
                    "button:has-text('Allow')",
                    "[data-qa='oauth_submit_button']",
                ],
                "postcondition": "Redirected to OAuth & Permissions page; xoxb-... token is visible",
            },
            {
                "id": "capture_bot_token",
                "action": "browser_evaluate",
                "description": "Read the Bot User OAuth Token off the page",
                "function": (
                    "() => { "
                    "const el = document.querySelector(\"[data-qa='bot_user_oauth_token'] code, "
                    "input[value^='xoxb-'], code:has-text('xoxb-')\"); "
                    "if (!el) return null; "
                    "return el.value || el.textContent || el.innerText; "
                    "}"
                ),
                "capture": {"bot_token": "starts_with:xoxb-"},
                "postcondition": "Captured bot_token starts with 'xoxb-'",
                "on_failure": {
                    "do": "collaborative_handoff",
                    "reason": "Token field not found — Slack UI may have changed; user picks the field",
                },
            },
            {
                "id": "open_basic_info",
                "action": "browser_click",
                "description": "Navigate to Basic Information for App-Level Tokens",
                "selectors_hint": ["a:has-text('Basic Information')"],
                "postcondition": "Basic Information page; 'App-Level Tokens' section visible",
            },
            {
                "id": "generate_app_token",
                "action": "browser_click",
                "description": "Click 'Generate Token and Scopes' under App-Level Tokens",
                "selectors_hint": [
                    "button:has-text('Generate Token and Scopes')",
                    "[data-qa='create_app_token']",
                ],
                "postcondition": "Token-generation modal visible",
            },
            {
                "id": "name_and_scope_app_token",
                "action": "browser_form_fill",
                "description": "Name token 'socket-mode' and add 'connections:write' scope",
                "fields": {
                    "token name": "socket-mode",
                    "scopes": ["connections:write"],
                },
                "selectors_hint": [
                    "input[name='token_name']",
                    "input[placeholder*='token']",
                ],
                "postcondition": "Both name and scope appear in the modal preview",
            },
            {
                "id": "submit_app_token",
                "action": "browser_click",
                "description": "Click Generate to mint the App-Level Token",
                "selectors_hint": ["button:has-text('Generate')"],
                "postcondition": "Modal shows xapp-... token (one-time display)",
            },
            {
                "id": "capture_app_token",
                "action": "browser_evaluate",
                "description": "Read the xapp-... token from the modal before it's dismissed",
                "function": (
                    "() => { "
                    "const el = document.querySelector(\"code:has-text('xapp-'), "
                    "input[value^='xapp-']\"); "
                    "if (!el) return null; "
                    "return el.value || el.textContent || el.innerText; "
                    "}"
                ),
                "capture": {"app_token": "starts_with:xapp-"},
                "postcondition": "Captured app_token starts with 'xapp-'",
            },
            {
                "id": "finalize",
                "action": "python",
                "description": (
                    "Call register.register_agent_identity(agent_name, "
                    "bot_token=captures['bot_token'], app_token=captures['app_token'], "
                    "user_id=<your slack user id from config>)"
                ),
                "postcondition": "config.yaml updated, Keychain entries present, confirmation DM sent",
            },
        ],
    }


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Slack app provisioning planner")
    parser.add_argument("--agent", required=True, help="Agent name (e.g. bhaga)")
    parser.add_argument(
        "--workspace",
        default="jarvis-coa3805",
        help="Slack workspace slug (default: jarvis-coa3805)",
    )
    parser.add_argument("--description", default="", help="Short tagline for the bot")
    parser.add_argument(
        "--print-manifest-only",
        action="store_true",
        help="Just render and print the manifest YAML, no plan",
    )
    args = parser.parse_args()

    if args.print_manifest_only:
        manifest, source = resolve_agent_manifest(args.agent.upper(), args.description)
        print(f"# manifest source: {source}")
        print(manifest)
    else:
        plan = build_plan(args.agent.upper(), args.workspace, args.description)
        print(json.dumps(plan, indent=2))
