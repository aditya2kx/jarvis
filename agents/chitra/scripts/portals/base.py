#!/usr/bin/env python3
"""Portal navigation framework — structured configs + orchestration.

Each portal module (schwab.py, etrade.py, etc.) exports a PORTAL_CONFIG dict
with structured data: URLs, selectors, flow steps, quirks. This base module
provides:

  1. PORTAL_CONFIG schema documentation
  2. Portal loader (by name → module → config)
  3. Plan generator (config → step-by-step AI instructions)
  4. Portal registry (discover all available portal modules)

Architecture:
  ┌─────────────┐     ┌───────────────┐     ┌──────────────┐
  │ PORTAL_CONFIG│────>│  base.py      │────>│ PortalSession│
  │ (navigation │     │  (planner /   │     │ (credentials,│
  │  knowledge)  │     │   loader)     │     │  OTP, upload)│
  └─────────────┘     └───────────────┘     └──────────────┘
         │                    │                      │
    checked in          checked in              checked in
    per-portal          generic                 generic
"""

import importlib
import os
import pkgutil
import sys

PORTALS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PORTALS_DIR, '..', '..', '..', '..'))


PORTAL_CONFIG_SCHEMA = """
PORTAL_CONFIG = {
    # === Identity ===
    "name": str,              # Human-readable name (e.g. "Charles Schwab")
    "keychain_service": str,  # macOS Keychain service (e.g. "jarvis-schwab")
    "login_required": bool,   # False for public sites (county CADs)

    # === URLs ===
    "urls": {
        "login": str,         # Login page URL
        "tax_docs": str,      # Direct URL to tax documents section
        "logout": str,        # Logout URL (optional)
        # ... any portal-specific URLs
    },

    # === Login Flow ===
    "login": {
        "method": str,        # "form" | "oauth" | "none"
        "quirks": [str],      # Things the AI must know (e.g. "form is in iframe")
        "fields": {
            "username": {"hint": str, "context": str},
            "password": {"hint": str, "context": str},
        },
        "submit": {"hint": str, "context": str},
        "post_submit_wait": int,     # seconds to wait after submit
        "success_indicator": str,     # URL pattern or text to verify login success
    },

    # === MFA ===
    "mfa": {
        "likelihood": str,    # "always" | "conditional" | "never"
        "methods": [str],     # ["sms", "email", "app", "phone_call"]
        "preferred": str,     # which method to pick if given a choice
        "device_trust": bool, # can we "remember this device"?
        "trigger_hint": str,  # what the MFA page looks like
    },

    # === Documents ===
    "documents": [
        {
            "type": str,              # e.g. "1099", "W-2", "Property Tax Bill"
            "name_pattern": str,      # e.g. "1099 Composite - {year}"
            "location_hint": str,     # where to find it on the site
            "per_account": bool,      # True if each account has its own copy
            "download_format": str,   # "PDF" | "CSV" | "HTML"
            "download_hint": str,     # what to click to download
        },
    ],

    # === Account Switching (if portal has multiple accounts) ===
    "account_selector": {
        "exists": bool,
        "hint": str,          # how to find/activate the selector
        "wait_after_switch": int,  # seconds to wait after switching
    },

    # === Search (for public sites like county CADs) ===
    "search": {
        "method": str,        # "address" | "property_id" | "owner_name"
        "fields": {str: str}, # field name → hint
        "submit_hint": str,
        "results_hint": str,  # what the results table looks like
    },

    # === Quirks & Known Issues ===
    "quirks": [str],          # battle-tested observations

    # === Logout ===
    "logout": {
        "url": str,
        "confirm_text": str,  # text that confirms successful logout
    },
}
"""


def load_portal(name):
    """Load a portal module by name and return its PORTAL_CONFIG.

    Args:
        name: Portal module name (e.g. "schwab", "etrade", "county_property_tax")

    Returns:
        dict: The portal's PORTAL_CONFIG

    Raises:
        ValueError: If the module doesn't exist or lacks PORTAL_CONFIG
    """
    try:
        module = importlib.import_module(f"agents.chitra.scripts.portals.{name}")
    except ImportError:
        full_path = os.path.join(PORTALS_DIR, f"{name}.py")
        if not os.path.exists(full_path):
            raise ValueError(f"No portal module found: {name}")
        spec = importlib.util.spec_from_file_location(name, full_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

    config = getattr(module, "PORTAL_CONFIG", None)
    if config is None:
        raise ValueError(
            f"Portal module '{name}' exists but has no PORTAL_CONFIG. "
            f"It may be using the old docstring format — needs migration."
        )
    return config


def list_portals():
    """Discover all portal modules in the portals directory.

    Returns:
        list of dicts: [{"module": str, "name": str, "has_config": bool}, ...]
    """
    portals = []
    for filename in sorted(os.listdir(PORTALS_DIR)):
        if filename.startswith("_") or filename == "base.py" or not filename.endswith(".py"):
            continue
        module_name = filename[:-3]
        try:
            config = load_portal(module_name)
            portals.append({
                "module": module_name,
                "name": config.get("name", module_name),
                "login_required": config.get("login_required", True),
                "doc_types": [d["type"] for d in config.get("documents", [])],
                "mfa": config.get("mfa", {}).get("likelihood", "unknown"),
                "has_config": True,
            })
        except ValueError:
            portals.append({
                "module": module_name,
                "name": module_name,
                "has_config": False,
            })
    return portals


def generate_plan(config, action="download_tax_docs"):
    """Generate a step-by-step execution plan from a PORTAL_CONFIG.

    This is what the AI agent reads to know exactly what to do.

    Args:
        config: PORTAL_CONFIG dict
        action: "download_tax_docs" | "search_property" | "check_status"

    Returns:
        list of step dicts: [{"step": int, "action": str, "details": str, ...}, ...]
    """
    steps = []
    step_num = 1
    name = config["name"]

    # Step 0: Prerequisites
    steps.append({
        "step": step_num,
        "action": "setup",
        "description": f"Initialize PortalSession('{name}')",
        "code": f"session = PortalSession('{config.get('keychain_service', name.lower())}')",
    })
    step_num += 1

    # Login flow
    if config.get("login_required", True):
        steps.append({
            "step": step_num,
            "action": "get_credentials",
            "description": "Retrieve credentials from Keychain",
            "code": "creds = session.get_credentials()",
        })
        step_num += 1

        login = config.get("login", {})
        steps.append({
            "step": step_num,
            "action": "navigate",
            "description": f"Go to login page",
            "url": config.get("urls", {}).get("login", ""),
            "quirks": login.get("quirks", []),
        })
        step_num += 1

        for field_name, field_info in login.get("fields", {}).items():
            hint = field_info if isinstance(field_info, str) else field_info.get("hint", field_name)
            context = field_info.get("context", "") if isinstance(field_info, dict) else ""
            steps.append({
                "step": step_num,
                "action": "fill_field",
                "field": field_name,
                "selector_hint": hint,
                "context": context,
                "value_from": f"creds['{field_name}']",
            })
            step_num += 1

        submit = login.get("submit", {})
        steps.append({
            "step": step_num,
            "action": "click",
            "selector_hint": submit.get("hint", "Submit / Log in button") if isinstance(submit, dict) else submit,
            "context": submit.get("context", "") if isinstance(submit, dict) else "",
        })
        step_num += 1

        wait = login.get("post_submit_wait", 10)
        steps.append({
            "step": step_num,
            "action": "wait",
            "seconds": wait,
            "reason": "Login processing and redirects",
        })
        step_num += 1

        # MFA handling
        mfa = config.get("mfa", {})
        if mfa.get("likelihood") in ("always", "conditional"):
            steps.append({
                "step": step_num,
                "action": "check_mfa",
                "likelihood": mfa["likelihood"],
                "methods": mfa.get("methods", []),
                "preferred": mfa.get("preferred", "email"),
                "device_trust": mfa.get("device_trust", False),
                "trigger_hint": mfa.get("trigger_hint", "verification code prompt"),
                "description": (
                    f"MFA is {mfa['likelihood']}. "
                    f"Methods: {', '.join(mfa.get('methods', ['unknown']))}. "
                    f"Preferred: {mfa.get('preferred', 'email')}."
                ),
                "if_triggered": [
                    "session.request_otp(phone_hint=<masked phone from page>)",
                    "Fill OTP field with returned code",
                    "Select 'remember this device' if available" if mfa.get("device_trust") else "",
                    "Submit",
                ],
            })
            step_num += 1

        # Verify login
        indicator = login.get("success_indicator", "")
        steps.append({
            "step": step_num,
            "action": "verify_login",
            "success_indicator": indicator,
            "description": f"Confirm login succeeded: {indicator}",
        })
        step_num += 1

    # Search flow (for public sites)
    search = config.get("search", {})
    if search:
        steps.append({
            "step": step_num,
            "action": "navigate",
            "url": config.get("urls", {}).get("search", config.get("urls", {}).get("login", "")),
            "description": "Go to search page",
        })
        step_num += 1

        steps.append({
            "step": step_num,
            "action": "search",
            "method": search.get("method", "address"),
            "fields": search.get("fields", {}),
            "submit_hint": search.get("submit_hint", "Search button"),
            "results_hint": search.get("results_hint", "Results table"),
            "description": f"Search by {search.get('method', 'address')}",
        })
        step_num += 1

    # Document retrieval
    if action == "download_tax_docs":
        docs_url = config.get("urls", {}).get("tax_docs")
        if docs_url:
            steps.append({
                "step": step_num,
                "action": "navigate",
                "url": docs_url,
                "description": "Navigate to tax documents section",
                "wait_after": 5,
            })
            step_num += 1

        account_sel = config.get("account_selector", {})
        if account_sel.get("exists"):
            steps.append({
                "step": step_num,
                "action": "note",
                "description": (
                    f"Multiple accounts possible. "
                    f"Account selector: {account_sel.get('hint', 'look for account dropdown')}. "
                    f"Download docs for EACH account."
                ),
                "wait_after_switch": account_sel.get("wait_after_switch", 5),
            })
            step_num += 1

        for doc in config.get("documents", []):
            steps.append({
                "step": step_num,
                "action": "download_document",
                "doc_type": doc["type"],
                "name_pattern": doc.get("name_pattern", doc["type"]),
                "location_hint": doc.get("location_hint", ""),
                "download_hint": doc.get("download_hint", "Download button"),
                "format": doc.get("download_format", "PDF"),
                "per_account": doc.get("per_account", False),
                "description": f"Download {doc['type']}: {doc.get('name_pattern', '')}",
                "stage_code": f"session.stage_download(path, doc_type='{doc['type']}')",
            })
            step_num += 1

    # Upload
    steps.append({
        "step": step_num,
        "action": "upload",
        "description": "Upload all staged files to Google Drive",
        "code": "session.upload_all()",
    })
    step_num += 1

    # Logout
    logout = config.get("logout", {})
    if logout.get("url"):
        steps.append({
            "step": step_num,
            "action": "logout",
            "url": logout["url"],
            "confirm_text": logout.get("confirm_text", ""),
        })
        step_num += 1

    # Notify
    steps.append({
        "step": step_num,
        "action": "notify",
        "description": f"Send status to user via Slack",
        "code": f"session.notify_status('Completed {name} — downloaded N documents')",
    })

    return steps


def format_plan_markdown(config, steps):
    """Format an execution plan as readable markdown for the AI agent."""
    lines = [
        f"# Portal Automation Plan: {config['name']}",
        "",
    ]

    if config.get("quirks"):
        lines.append("## Known Quirks (read these first!)")
        for q in config["quirks"]:
            lines.append(f"- {q}")
        lines.append("")

    lines.append("## Steps")
    lines.append("")

    for s in steps:
        action = s["action"]
        desc = s.get("description", action)
        lines.append(f"### Step {s['step']}: {desc}")

        if s.get("url"):
            lines.append(f"  URL: `{s['url']}`")
        if s.get("code"):
            lines.append(f"  ```python\n  {s['code']}\n  ```")
        if s.get("quirks"):
            for q in s["quirks"]:
                lines.append(f"  **Quirk:** {q}")
        if s.get("selector_hint"):
            ctx = f" ({s['context']})" if s.get("context") else ""
            lines.append(f"  Look for: \"{s['selector_hint']}\"{ctx}")
        if s.get("if_triggered"):
            lines.append(f"  If MFA triggered:")
            for sub in s["if_triggered"]:
                if sub:
                    lines.append(f"    - {sub}")
        if s.get("seconds"):
            lines.append(f"  Wait: {s['seconds']}s — {s.get('reason', '')}")
        if s.get("fields"):
            for fname, fhint in s["fields"].items():
                lines.append(f"  Field: {fname} → \"{fhint}\"")

        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    print("Available portal modules:\n")
    for p in list_portals():
        status = "structured" if p["has_config"] else "legacy (needs migration)"
        docs = ", ".join(p.get("doc_types", [])) if p.get("doc_types") else "—"
        print(f"  {p['module']:25s} {p['name']:25s} [{status}]  docs: {docs}")
