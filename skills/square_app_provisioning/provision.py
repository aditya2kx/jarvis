#!/usr/bin/env python3
"""Square app provisioning — Playwright playbook builder for developer.squareup.com.

Mirror of `skills/slack_app_provisioning/provision.py`. Produces a structured
plan of browser_navigate / browser_snapshot / browser_click / browser_evaluate
steps the AI executes against the `user-playwright` MCP.

Why a "playbook" rather than executing Playwright directly: same reasoning as
the Slack equivalent — Playwright is invoked through the MCP layer by the AI
agent itself, not by a Python subprocess. The skill cannot call browser_navigate
from Python. What it CAN do is produce a deterministic, reviewable, reusable
sequence of MCP-tool calls + the structured arguments + the post-conditions to
check at each step.
"""

from __future__ import annotations

import json
from typing import Optional


DASHBOARD_URL = "https://developer.squareup.com/apps"
LOCATIONS_URL = "https://app.squareup.com/dashboard/locations"


def build_plan(
    store: str,
    app_name: Optional[str] = None,
    timezone: str = "America/Chicago",
    location_match: Optional[str] = None,
) -> dict:
    """Produce a structured plan for the AI to drive against `user-playwright`.

    Args:
        store: Logical store name used for Keychain entry + store-profile JSON
               (e.g. "austin", "houston"). Lowercased for downstream identifiers.
        app_name: Display name for the Square Application (e.g. "Jarvis BHAGA Austin").
                  Defaults to f"Jarvis BHAGA {store.capitalize()}".
        timezone: Local timezone for the store, persisted into store-profile JSON.
                  Used by `skills/square_tips/` to bucket payments by local date.
        location_match: Substring (case-insensitive) to match against location
                        names on the Locations page. Defaults to `store`.

    Returns:
        Dict with structured steps + a `captures` slot the AI fills as it goes.
    """
    s = store.lower()
    name = app_name or f"Jarvis BHAGA {store.capitalize()}"
    match = (location_match or store).lower()

    return {
        "store": s,
        "app_name": name,
        "timezone": timezone,
        "location_match": match,
        "captures": {
            # AI fills these as it drives Playwright; register.register_store
            # consumes the final dict.
            "access_token": None,        # EAA... (production PAT)
            "application_id": None,       # sq0idp-...
            "location_id": None,          # L...
        },
        "steps": [
            {
                "id": "open_apps_index",
                "action": "browser_navigate",
                "description": "Open Square Developer Dashboard apps index",
                "args": {"url": DASHBOARD_URL},
                "postcondition": (
                    "URL contains developer.squareup.com/apps and the user is "
                    "signed in (no /signin redirect)."
                ),
                "on_failure": {
                    "if": "redirected to /signin or login form visible",
                    "do": "collaborative_handoff",
                    "reason": "Square Dashboard session expired — user must log in once",
                },
            },
            {
                "id": "click_create_app",
                "action": "browser_snapshot_then_click",
                "description": "Click '+', 'Create your first application', or 'Add Application'",
                "selectors_hint": [
                    "button:has-text('Create your first application')",
                    "button:has-text('Add Application')",
                    "a:has-text('Add Application')",
                    "[data-test='add-application-button']",
                    "button[aria-label*='Create' i]",
                ],
                "postcondition": "App-creation modal/page is visible, app-name input present",
            },
            {
                "id": "name_app",
                "action": "browser_type",
                "description": f"Enter the application name: {name!r}",
                "selectors_hint": [
                    "input[name='name']",
                    "input[placeholder*='application name' i]",
                    "input[aria-label*='application name' i]",
                    "input[type='text']",
                ],
                "value": name,
                "postcondition": "App-name input contains the typed name",
            },
            {
                "id": "submit_create_app",
                "action": "browser_click",
                "description": "Submit the new app form",
                "selectors_hint": [
                    "button:has-text('Save')",
                    "button:has-text('Create')",
                    "button[type='submit']",
                ],
                "postcondition": (
                    "Landed on the new app's settings page (URL contains "
                    "/apps/sq0idp-... or similar)."
                ),
                "capture": {"application_id": "regex:/apps/(sq0idp-[A-Za-z0-9_-]+)"},
            },
            {
                "id": "open_production_credentials",
                "action": "browser_snapshot_then_click",
                "description": (
                    "Switch to the Production credentials tab/section. Square's "
                    "dashboard sometimes defaults to Sandbox; we always want Production "
                    "for real shop data. May be a tab labeled 'Production', a toggle, "
                    "or the default view depending on Square's current UI."
                ),
                "selectors_hint": [
                    "button:has-text('Production')",
                    "[role='tab']:has-text('Production')",
                    "a:has-text('Credentials')",
                    "a:has-text('Production')",
                    "[aria-label='Production']",
                ],
                "postcondition": (
                    "Production credentials section is visible, including a 'Personal "
                    "Access Token' field (often hidden behind a 'Show' button)."
                ),
            },
            {
                "id": "reveal_pat",
                "action": "browser_click",
                "description": "Click 'Show' / 'Reveal' next to Personal Access Token",
                "selectors_hint": [
                    "button:has-text('Show')",
                    "button:has-text('Reveal')",
                    "button[aria-label*='Show' i]",
                    "button[aria-label*='Reveal' i]",
                ],
                "postcondition": "PAT value is visible in the DOM (starts with 'EAA')",
            },
            {
                "id": "capture_pat",
                "action": "browser_evaluate",
                "description": "Read the Personal Access Token off the page",
                "function": (
                    "() => { "
                    "  const inputs = Array.from(document.querySelectorAll("
                    "    'input, textarea, code, span, div'"
                    "  )); "
                    "  for (const el of inputs) { "
                    "    const v = el.value || el.textContent || el.innerText || ''; "
                    "    if (v.startsWith('EAA')) return v.trim(); "
                    "  } "
                    "  return null; "
                    "}"
                ),
                "capture": {"access_token": "starts_with:EAA"},
                "postcondition": "Captured access_token starts with 'EAA'",
                "on_failure": {
                    "do": "collaborative_handoff",
                    "reason": (
                        "Token field not found — Square UI may have changed; user "
                        "picks the field, AI persists the new selector to "
                        "agents/bhaga/knowledge-base/selectors/square_admin.json"
                    ),
                },
            },
            {
                "id": "open_locations",
                "action": "browser_navigate",
                "description": "Navigate to Square Dashboard Locations page",
                "args": {"url": LOCATIONS_URL},
                "postcondition": (
                    "Locations table is visible with one row per shop location. "
                    "Each row exposes the location_id (often visible directly, "
                    "or shown after clicking into the row)."
                ),
            },
            {
                "id": "find_location_id",
                "action": "browser_evaluate",
                "description": (
                    f"Find the location row matching {match!r} (case-insensitive), "
                    f"extract its location_id (Square location IDs start with 'L' "
                    f"and are 13 chars total: e.g. 'L3X8YZQT9PJ8E'). The id may be "
                    f"in a data-* attribute, a hidden input, an inline label, or "
                    f"only revealed after clicking into the location's detail page."
                ),
                "function": (
                    "(matchStr) => { "
                    "  const m = matchStr.toLowerCase(); "
                    "  // Pass 1: rows with a visible L... id near the matched name "
                    "  const rows = Array.from(document.querySelectorAll('tr, li, [role=row], [data-test*=location]')); "
                    "  for (const row of rows) { "
                    "    const text = (row.textContent || '').toLowerCase(); "
                    "    if (!text.includes(m)) continue; "
                    "    const idMatch = (row.textContent || '').match(/\\bL[A-Z0-9]{12}\\b/); "
                    "    if (idMatch) return idMatch[0]; "
                    "    const dataLoc = row.querySelector('[data-location-id], [data-test-location-id]'); "
                    "    if (dataLoc) return dataLoc.getAttribute('data-location-id') || dataLoc.getAttribute('data-test-location-id'); "
                    "  } "
                    "  // Pass 2: scan the full DOM for any L... id (only safe if 1 location) "
                    "  const allText = document.body.innerText; "
                    "  const all = allText.match(/\\bL[A-Z0-9]{12}\\b/g) || []; "
                    "  if (all.length === 1) return all[0]; "
                    "  return null; "
                    "}"
                ),
                "args": {"arg": match},
                "capture": {"location_id": "starts_with:L"},
                "postcondition": "Captured location_id matches /^L[A-Z0-9]{12}$/",
                "on_failure": {
                    "do": "collaborative_handoff",
                    "reason": (
                        "Couldn't find the location_id from the Locations page — user "
                        "clicks into the right location, AI captures from the URL or "
                        "details panel and persists the new selector."
                    ),
                },
            },
            {
                "id": "finalize",
                "action": "python",
                "description": (
                    f"Call register.register_store(store={s!r}, "
                    f"access_token=captures['access_token'], "
                    f"application_id=captures['application_id'], "
                    f"location_id=captures['location_id'], "
                    f"timezone={timezone!r})"
                ),
                "postcondition": (
                    "Keychain entry exists, store-profile JSON written, "
                    "BHAGA Slack confirmation DM sent."
                ),
            },
        ],
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Square app provisioning planner")
    parser.add_argument("--store", required=True, help="Logical store name (e.g. austin)")
    parser.add_argument("--app-name", default=None, help="Square app display name override")
    parser.add_argument("--timezone", default="America/Chicago", help="Store local timezone")
    parser.add_argument("--location-match", default=None,
                        help="Substring match for the Locations page (defaults to --store)")
    args = parser.parse_args()
    plan = build_plan(
        store=args.store,
        app_name=args.app_name,
        timezone=args.timezone,
        location_match=args.location_match,
    )
    print(json.dumps(plan, indent=2))
