#!/usr/bin/env python3
"""Portal task orchestrator — runs the full collect→credential→plan→execute loop.

This is CHITRA's main execution engine. Given a registry of documents and a
list of portal tasks, it:

  1. Resolves each document's issuer to a portal navigation module
  2. Checks Keychain for credentials — if missing, asks user via Slack
  3. Generates step-by-step execution plans from the portal configs
  4. Provides the plans to the AI agent for Playwright-based execution
  5. Tracks progress and sends status updates via Slack

The orchestrator does NOT drive Playwright directly — it prepares everything
so the AI agent (CHITRA) can follow the plans using Playwright MCP tools.

Usage (by CHITRA AI agent):
    from agents.chitra.scripts.run_portal_tasks import TaskRunner
    runner = TaskRunner()
    ready = runner.prepare_all()    # checks creds, generates plans
    for task in ready:
        # AI follows task["plan"] using Playwright MCP
        runner.mark_complete(task["portal"])

Usage (CLI for debugging):
    python run_portal_tasks.py --check          # show cred status for all portals
    python run_portal_tasks.py --plan schwab    # generate plan for one portal
    python run_portal_tasks.py --prepare        # prepare all tasks (check creds, gen plans)
"""

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
from core.config_loader import load_config, project_dir


class TaskRunner:
    """Orchestrates portal task execution with credential management."""

    def __init__(self):
        self._config = load_config()
        self._issuer_to_module = self._build_issuer_map()

    # ── Issuer → Portal module resolution ──────────────────────────

    def _build_issuer_map(self):
        """Build a mapping from issuer names to portal module names.

        Uses both the navigation module names and common aliases so that
        'Charles Schwab & Co., Inc' resolves to the 'schwab' module.
        """
        from agents.chitra.scripts.portals.base import list_portals

        aliases = {
            "schwab": ["charles schwab", "schwab"],
            "etrade": ["e*trade", "etrade", "morgan stanley"],
            "robinhood": ["robinhood"],
            "fidelity": ["fidelity", "netbenefits"],
            "wells_fargo": ["wells fargo", "wellsfargo"],
            "chase": ["chase", "jpmorgan", "jp morgan"],
            "hsa_bank": ["hsa bank", "healthequity", "optum", "lively"],
            "homebase": ["homebase"],
            "county_property_tax": ["county tax", "county appraisal", "cad"],
        }

        issuer_map = {}
        for module_name, alias_list in aliases.items():
            for alias in alias_list:
                issuer_map[alias] = module_name

        return issuer_map

    def resolve_portal(self, issuer):
        """Resolve an issuer name to a portal module name.

        Args:
            issuer: Issuer string from the document registry (e.g. "Charles Schwab & Co")

        Returns:
            Portal module name (e.g. "schwab") or None if no match
        """
        issuer_lower = issuer.lower().strip()
        for alias, module in self._issuer_to_module.items():
            if alias in issuer_lower:
                return module
        return None

    # ── Credential management ──────────────────────────────────────

    def check_credentials(self, portal_module):
        """Check if credentials exist in Keychain for a portal.

        Args:
            portal_module: Module name (e.g. "schwab")

        Returns:
            dict with 'exists', 'username', 'keychain_service'
        """
        from agents.chitra.scripts.portals.base import load_portal

        try:
            config = load_portal(portal_module)
        except ValueError:
            return {"exists": False, "keychain_service": None, "reason": "no module"}

        service = config.get("keychain_service")
        if not service:
            return {"exists": True, "keychain_service": None, "reason": "no login required"}

        cmd = f"security find-generic-password -s {service} 2>&1"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
        combined = result.stdout + result.stderr

        if "could not be found" in combined or result.returncode != 0:
            return {
                "exists": False,
                "keychain_service": service,
                "portal_name": config.get("name", portal_module),
            }

        username = None
        for line in combined.split("\n"):
            if '"acct"' in line:
                parts = line.split('"')
                if len(parts) >= 4:
                    username = parts[-2]
                    break

        return {
            "exists": True,
            "keychain_service": service,
            "username": username,
            "portal_name": config.get("name", portal_module),
        }

    def check_all_credentials(self):
        """Check credential status for all portal modules that require login.

        Returns:
            list of dicts with portal status info
        """
        from agents.chitra.scripts.portals.base import list_portals

        results = []
        for p in list_portals():
            if not p.get("has_config"):
                continue
            if not p.get("login_required", True):
                results.append({
                    "module": p["module"],
                    "name": p["name"],
                    "login_required": False,
                    "creds_status": "n/a",
                })
                continue

            cred_info = self.check_credentials(p["module"])
            results.append({
                "module": p["module"],
                "name": p["name"],
                "login_required": True,
                "creds_status": "stored" if cred_info["exists"] else "missing",
                "username": cred_info.get("username"),
                "keychain_service": cred_info.get("keychain_service"),
            })

        return results

    def store_credentials(self, portal_module, username, password):
        """Store credentials in macOS Keychain for a portal.

        Args:
            portal_module: Module name (e.g. "schwab")
            username: Username/email
            password: Password

        Returns:
            True if stored successfully
        """
        from agents.chitra.scripts.portals.base import load_portal

        config = load_portal(portal_module)
        service = config.get("keychain_service")
        if not service:
            return False

        subprocess.run(
            ["security", "delete-generic-password", "-s", service, "-a", username],
            capture_output=True,
        )
        result = subprocess.run(
            ["security", "add-generic-password", "-s", service, "-a", username, "-w", password],
            capture_output=True, text=True,
        )
        return result.returncode == 0

    def request_credentials_via_slack(self, portal_module):
        """Ask the user for credentials via Slack DM.

        Username is collected via Slack (low sensitivity).
        Password NEVER transits through Slack — instead, the bot sends a
        one-liner Keychain command that the user pastes into any terminal.
        This avoids passwords sitting in Slack chat history.

        Flow:
          1. Bot DMs: "I need Chase creds. Reply with your username."
          2. User replies: "aditya2kx"
          3. Bot DMs: "Got it. Now run this command in any terminal:
                       security add-generic-password -s jarvis-chase -a aditya2kx -w 'YOUR_PASSWORD'"
          4. User runs the command (terminal, SSH, whatever)
          5. Bot polls Keychain to confirm storage, then DMs: "All set!"

        Args:
            portal_module: Module name (e.g. "schwab")

        Returns:
            dict with 'username' and 'stored' status, or None if timed out
        """
        from agents.chitra.scripts.portals.base import load_portal
        from skills.slack.adapter import send_message, read_replies, open_dm

        config = load_portal(portal_module)
        portal_name = config.get("name", portal_module)
        service = config.get("keychain_service")
        login_url = config.get("urls", {}).get("login", "")

        user_id = self._config.get("slack", {}).get("primary_user_id")
        if not user_id:
            print(f"[runner] No slack.primary_user_id in config")
            return None

        dm_channel = open_dm(user_id)

        msg = send_message(
            dm_channel,
            f":key: *Credentials Needed — {portal_name}*\n\n"
            f"I need to log into {portal_name} to download your tax documents.\n"
            f"Login URL: {login_url}\n\n"
            f"Please reply with your *username* for this portal.",
        )
        sent_ts = msg["ts"]

        username = self._wait_for_reply(dm_channel, user_id, sent_ts, timeout=600)
        if not username:
            send_message(dm_channel, f":x: Timed out waiting for {portal_name} username.")
            return None

        username_text = username["text"].strip()

        send_message(
            dm_channel,
            f":lock: Got username: `{username_text}`\n\n"
            f"Now I need the password — but *passwords never go through Slack*.\n"
            f"Run this in any terminal (Mac, SSH, etc.):\n\n"
            f"```security add-generic-password -s {service} -a {username_text} -w 'YOUR_PASSWORD_HERE'```\n\n"
            f"Replace `YOUR_PASSWORD_HERE` with your actual password.\n"
            f"I'll confirm once I detect it in Keychain.",
        )

        stored = self._poll_keychain(service, timeout=600, poll_interval=5)

        if stored:
            send_message(
                dm_channel,
                f":white_check_mark: *{portal_name}* credentials confirmed in Keychain!\n"
                f"Username: `{username_text}` | Service: `{service}`",
            )
            return {"username": username_text, "stored": True, "service": service}
        else:
            send_message(
                dm_channel,
                f":x: Timed out waiting for {portal_name} password in Keychain.\n"
                f"You can store it later:\n"
                f"```security add-generic-password -s {service} -a {username_text} -w 'YOUR_PASSWORD'```",
            )
            return {"username": username_text, "stored": False}

    def _wait_for_reply(self, channel, user_id, after_ts, timeout=600, poll_interval=5):
        """Poll for a user reply in a DM channel after a given timestamp."""
        from skills.slack.adapter import read_replies

        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(poll_interval)
            try:
                messages = read_replies(channel, oldest=after_ts, limit=5)
                for msg in messages:
                    if (msg["ts"] != after_ts and
                            msg.get("user") == user_id and
                            not msg.get("bot_id")):
                        return msg
            except Exception as e:
                print(f"[runner] Poll error: {e}")
        return None

    def _poll_keychain(self, service, timeout=600, poll_interval=5):
        """Poll macOS Keychain until a credential appears for the given service."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(poll_interval)
            cmd = f"security find-generic-password -s {service} 2>&1"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
            combined = result.stdout + result.stderr
            if "could not be found" not in combined and result.returncode == 0:
                return True
        return False

    def ensure_credentials(self, portal_module, interactive=True, method="collaborative"):
        """Ensure credentials exist for a portal, requesting them if needed.

        Args:
            portal_module: Module name
            interactive: If True, engage user when missing. If False, just report.
            method: "collaborative" (user enters in browser, AI captures) |
                    "slack" (ask via Slack DM + Keychain CLI)

        Returns:
            dict with 'ready' bool, 'credential_mode' for plan generation, and status info
        """
        cred = self.check_credentials(portal_module)

        if cred.get("reason") == "no login required":
            return {"ready": True, "reason": "no login required", "credential_mode": "keychain"}

        if cred["exists"]:
            return {
                "ready": True,
                "username": cred.get("username"),
                "service": cred.get("keychain_service"),
                "credential_mode": "keychain",
            }

        if not interactive:
            return {
                "ready": False,
                "reason": "credentials missing — needs collaborative login",
                "service": cred.get("keychain_service"),
                "portal_name": cred.get("portal_name"),
                "credential_mode": "collaborative",
            }

        if method == "collaborative":
            print(f"[runner] Credentials missing for {portal_module} — will use collaborative browser login")
            return {
                "ready": True,
                "reason": "collaborative login required",
                "service": cred.get("keychain_service"),
                "portal_name": cred.get("portal_name"),
                "credential_mode": "collaborative",
            }

        print(f"[runner] Credentials missing for {portal_module} — requesting via Slack")
        result = self.request_credentials_via_slack(portal_module)
        if result and result.get("stored"):
            return {"ready": True, "username": result["username"], "service": result["service"], "credential_mode": "keychain"}

        return {"ready": False, "reason": "user did not provide credentials", "credential_mode": "collaborative"}

    # ── Plan generation & task preparation ─────────────────────────

    def prepare_task(self, portal_module, interactive=True, method="collaborative"):
        """Prepare a single portal task: check creds + generate plan.

        Args:
            portal_module: Module name (e.g. "schwab")
            interactive: Engage user when creds missing
            method: "collaborative" (browser capture) | "slack" (DM + Keychain CLI)

        Returns:
            dict with 'ready', 'plan', 'config', 'creds' info
        """
        from agents.chitra.scripts.portals.base import load_portal, generate_plan, format_plan_markdown

        cred_result = self.ensure_credentials(portal_module, interactive=interactive, method=method)
        if not cred_result.get("ready"):
            return {
                "portal": portal_module,
                "ready": False,
                "reason": cred_result.get("reason", "unknown"),
                "credential_mode": cred_result.get("credential_mode", "collaborative"),
                "plan": None,
            }

        credential_mode = cred_result.get("credential_mode", "keychain")

        try:
            config = load_portal(portal_module)
        except ValueError as e:
            return {"portal": portal_module, "ready": False, "reason": str(e), "plan": None}

        steps = generate_plan(config, credential_mode=credential_mode)
        markdown = format_plan_markdown(config, steps)

        return {
            "portal": portal_module,
            "ready": True,
            "credential_mode": credential_mode,
            "config": config,
            "plan_steps": steps,
            "plan_markdown": markdown,
            "username": cred_result.get("username"),
            "keychain_service": cred_result.get("service"),
        }

    def prepare_all(self, task_list=None, interactive=True, method="collaborative"):
        """Prepare all portal tasks from a task list.

        If no task_list is provided, generates one from the default registry
        using AnswerProcessor.

        Args:
            task_list: Output from AnswerProcessor.generate_portal_tasks()
            interactive: Engage user when creds missing
            method: "collaborative" (browser capture) | "slack" (DM + Keychain CLI)

        Returns:
            list of prepared task dicts, sorted by readiness (ready first)
        """
        if task_list is None:
            from agents.chitra.scripts.process_answers import AnswerProcessor
            processor = AnswerProcessor()
            task_list = processor.generate_portal_tasks()

        prepared = []
        seen_modules = set()

        for task in task_list:
            if task["automation_level"] in ("user_provides", "email_skill"):
                prepared.append({
                    "portal": task["source"],
                    "ready": False,
                    "reason": task["automation_level"],
                    "action": task["action"],
                    "documents": task["documents"],
                    "plan": None,
                })
                continue

            for module_name in task.get("portal_modules", []):
                if module_name in seen_modules:
                    continue
                seen_modules.add(module_name)

                result = self.prepare_task(module_name, interactive=interactive, method=method)
                result["documents"] = task["documents"]
                result["action"] = task.get("action", "")
                prepared.append(result)

            if not task.get("portal_modules"):
                for doc in task["documents"]:
                    module = self.resolve_portal(doc.get("issuer", ""))
                    if module and module not in seen_modules:
                        seen_modules.add(module)
                        result = self.prepare_task(module, interactive=interactive, method=method)
                        result["documents"] = [doc]
                        prepared.append(result)

        prepared.sort(key=lambda t: (0 if t.get("ready") else 1, t.get("portal", "")))
        return prepared

    # ── Progress tracking ──────────────────────────────────────────

    def mark_complete(self, portal_module, docs_downloaded=None):
        """Mark a portal task as complete and notify via Slack.

        Args:
            portal_module: Module name
            docs_downloaded: List of downloaded doc descriptions
        """
        from skills.slack.adapter import send_message

        dm_channel = self._config.get("slack", {}).get("dm_channel")
        if not dm_channel:
            return

        doc_list = ""
        if docs_downloaded:
            doc_list = "\n".join(f"  • {d}" for d in docs_downloaded)
            doc_list = f"\n{doc_list}"

        send_message(
            dm_channel,
            f":white_check_mark: *{portal_module}* — complete{doc_list}",
        )

    def send_status_summary(self, prepared_tasks):
        """Send a summary of all task statuses to the user via Slack.

        Args:
            prepared_tasks: Output from prepare_all()
        """
        from skills.slack.adapter import send_message

        dm_channel = self._config.get("slack", {}).get("dm_channel")
        if not dm_channel:
            return

        ready = [t for t in prepared_tasks if t.get("ready")]
        blocked = [t for t in prepared_tasks if not t.get("ready")]

        lines = [":clipboard: *Portal Task Summary*\n"]

        if ready:
            lines.append(f"*Ready to run ({len(ready)}):*")
            for t in ready:
                lines.append(f"  :white_check_mark: {t['portal']}")

        if blocked:
            lines.append(f"\n*Blocked ({len(blocked)}):*")
            for t in blocked:
                reason = t.get("reason", "unknown")
                lines.append(f"  :x: {t['portal']} — {reason}")

        send_message(dm_channel, "\n".join(lines))


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Portal task orchestrator")
    parser.add_argument("--check", action="store_true", help="Check credential status for all portals")
    parser.add_argument("--plan", type=str, help="Generate execution plan for a portal module")
    parser.add_argument("--prepare", action="store_true", help="Prepare all tasks (non-interactive)")
    parser.add_argument("--interactive", action="store_true", help="Engage user when creds missing")
    parser.add_argument("--collaborative", action="store_true", help="Use collaborative browser login for missing creds")
    args = parser.parse_args()

    method = "collaborative" if args.collaborative else "slack"
    runner = TaskRunner()

    if args.check:
        print("Portal Credential Status:\n")
        creds = runner.check_all_credentials()
        for c in creds:
            if not c["login_required"]:
                status = "public (no login)"
            elif c["creds_status"] == "stored":
                status = f"STORED  (user: {c.get('username', '?')})"
            else:
                status = f"MISSING (need: {c.get('keychain_service', '?')})"
            print(f"  {c['module']:25s} {c['name']:25s} {status}")

    elif args.plan:
        result = runner.prepare_task(args.plan, interactive=False, method=method)
        if result.get("ready"):
            print(result["plan_markdown"])
        else:
            print(f"Not ready: {result.get('reason')}")

    elif args.prepare:
        prepared = runner.prepare_all(interactive=args.interactive, method=method)
        print(f"Prepared {len(prepared)} tasks:\n")
        for t in prepared:
            mode = t.get("credential_mode", "?")
            status = f"READY ({mode})" if t.get("ready") else f"BLOCKED ({t.get('reason', '?')})"
            portal = t.get("portal", "?")
            docs = len(t.get("documents", []))
            print(f"  {portal:25s} [{status:40s}]  {docs} docs")
            if t.get("ready") and t.get("plan_steps"):
                print(f"    → {len(t['plan_steps'])} execution steps prepared")


if __name__ == "__main__":
    main()
