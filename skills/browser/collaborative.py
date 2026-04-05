#!/usr/bin/env python3
"""Collaborative browser session — AI drives, user assists when needed.

The AI and user share a headed Playwright browser. The AI automates
navigation but can pause for the user to:

  1. Enter credentials (AI captures from form fields, stores in Keychain)
  2. Navigate when the AI is stuck (AI watches snapshots and learns)
  3. Handle anything unexpected (CAPTCHAs, modals, complex flows)

Communication happens via Slack DM. The user sees the browser window
and interacts directly; the AI observes changes via Playwright MCP.

This module does NOT call Playwright MCP directly. It provides:
  - JS snippets for the AI to inject via browser_evaluate
  - Slack notification helpers
  - Keychain storage for captured credentials
  - Learning persistence for navigation patterns

Usage by CHITRA (AI agent):
    from skills.browser.collaborative import CollaborativeSession
    from skills.browser.portal_session import PortalSession

    portal = PortalSession("Schwab")
    collab = CollaborativeSession(portal)

    # Phase 1: Navigate to login page (AI uses browser_navigate)
    # Phase 2: Inject interceptor (AI uses browser_evaluate with collab.INTERCEPTOR_JS)
    # Phase 3: Notify user (collab.notify_credential_needed)
    # Phase 4: Poll for login completion (AI uses browser_evaluate with collab.POLL_STATE_JS)
    # Phase 5: Read captured creds (AI uses browser_evaluate with collab.READ_CREDS_JS)
    # Phase 6: Store them (collab.store_captured_credentials)
"""

import json
import os
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from core.config_loader import load_config, project_dir

LEARNINGS_DIR = pathlib.Path(project_dir()) / "agents" / "chitra" / "knowledge-base" / "learnings"

# ── JavaScript Snippets ────────────────────────────────────────────

INTERCEPTOR_JS = """
(function() {
  window.__jarvis_captured_creds = null;

  function storeCreds(creds) {
    window.__jarvis_captured_creds = creds;
    try { sessionStorage.setItem('__jarvis_creds', JSON.stringify(creds)); } catch(e) {}
    try { localStorage.setItem('__jarvis_creds', JSON.stringify(creds)); } catch(e) {}
  }

  function captureInputs(container) {
    var inputs = container.querySelectorAll('input');
    var creds = {};
    var found = false;
    inputs.forEach(function(inp) {
      var val = inp.value || '';
      if (!val) return;
      if (inp.type === 'password') { creds.password = val; found = true; }
      else if (inp.type === 'text' || inp.type === 'email' || inp.type === 'tel') {
        if (!creds.username) creds.username = val;
      }
    });
    if (found && creds.username) {
      storeCreds(creds);
    }
  }

  document.addEventListener('submit', function(e) {
    captureInputs(e.target);
  }, true);

  document.addEventListener('click', function(e) {
    var btn = e.target.closest('button, input[type="submit"], a[role="button"]');
    if (btn) {
      var form = btn.closest('form') || document;
      captureInputs(form);
    }
  }, true);

  document.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && e.target.type === 'password') {
      var form = e.target.closest('form') || document;
      captureInputs(form);
    }
  }, true);

  var checkInterval = setInterval(function() {
    if (window.__jarvis_captured_creds) {
      clearInterval(checkInterval);
      return;
    }
    var pwField = document.querySelector('input[type="password"]');
    if (pwField && pwField.value) {
      captureInputs(pwField.closest('form') || document);
    }
  }, 500);

  setTimeout(function() { clearInterval(checkInterval); }, 300000);
})()
"""

IFRAME_INTERCEPTOR_JS = """
(function() {
  window.__jarvis_captured_creds = null;
  var iframes = document.querySelectorAll('iframe');
  iframes.forEach(function(iframe) {
    try {
      var iframeDoc = iframe.contentDocument || iframe.contentWindow.document;
      iframeDoc.addEventListener('submit', function(e) {
        var inputs = e.target.querySelectorAll('input');
        var creds = {};
        inputs.forEach(function(inp) {
          var val = inp.value || '';
          if (!val) return;
          if (inp.type === 'password') creds.password = val;
          else if (inp.type === 'text' || inp.type === 'email' || inp.type === 'tel') {
            if (!creds.username) creds.username = val;
          }
        });
        if (creds.password && creds.username) window.__jarvis_captured_creds = creds;
      }, true);
    } catch(e) {}
  });
})()
"""

READ_CREDS_JS = """
(function() {
  var creds = window.__jarvis_captured_creds;
  if (!creds) try { creds = JSON.parse(sessionStorage.getItem('__jarvis_creds')); } catch(e) {}
  if (!creds) try { creds = JSON.parse(localStorage.getItem('__jarvis_creds')); } catch(e) {}
  if (creds) {
    try { sessionStorage.removeItem('__jarvis_creds'); } catch(e) {}
    try { localStorage.removeItem('__jarvis_creds'); } catch(e) {}
  }
  return JSON.stringify(creds || null);
})()
"""

POLL_STATE_JS = """
(function() {
  var creds = window.__jarvis_captured_creds;
  if (!creds) try { creds = JSON.parse(sessionStorage.getItem('__jarvis_creds')); } catch(e) {}
  if (!creds) try { creds = JSON.parse(localStorage.getItem('__jarvis_creds')); } catch(e) {}
  return JSON.stringify({
    url: window.location.href,
    hasPasswordField: !!document.querySelector('input[type="password"]'),
    capturedCreds: creds
  });
})()
"""


class CollaborativeSession:
    """Manages collaborative browser sessions between AI and user."""

    def __init__(self, portal_session):
        """
        Args:
            portal_session: A PortalSession instance for the portal being automated
        """
        self.portal = portal_session
        self._config = load_config()
        self._dm_channel = self._config.get("slack", {}).get("dm_channel")
        self._user_id = self._config.get("slack", {}).get("primary_user_id")

    # ── Credential Capture ─────────────────────────────────────────

    def notify_credential_needed(self, login_url=None):
        """Send Slack DM asking user to enter credentials in the browser.

        Returns the sent message timestamp (for threading follow-ups).
        """
        url_line = f"\nLogin URL: {login_url}" if login_url else ""
        return self._notify(
            f":desktop_computer: *Your turn — {self.portal.portal_name}*{url_line}\n\n"
            f"I've navigated to the login page in the browser on your Mac.\n"
            f"Please *enter your username and password* and click Login.\n\n"
            f"I'll capture the credentials and store them in Keychain "
            f"so you never need to enter them again for this portal."
        )

    def store_captured_credentials(self, username, password):
        """Store credentials captured from the browser form into Keychain.

        Returns True if stored successfully.
        """
        service = self.portal.keychain_service

        subprocess.run(
            ["security", "delete-generic-password", "-s", service, "-a", username],
            capture_output=True,
        )
        result = subprocess.run(
            ["security", "add-generic-password", "-s", service, "-a", username, "-w", password],
            capture_output=True, text=True,
        )
        stored = result.returncode == 0

        if stored:
            self._notify(
                f":white_check_mark: *{self.portal.portal_name}* credentials captured and "
                f"stored in Keychain.\n"
                f"Username: `{username}` | Service: `{service}`\n"
                f"I'll use these automatically from now on."
            )
        else:
            self._notify(
                f":x: Failed to store {self.portal.portal_name} credentials in Keychain.\n"
                f"Error: {result.stderr.strip()}"
            )

        return stored

    def has_stored_credentials(self):
        """Check if credentials already exist in Keychain for this portal."""
        service = self.portal.keychain_service
        if not service:
            return False
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0

    # ── Stuck Navigation / Takeover ────────────────────────────────

    def request_takeover(self, reason, screenshot_path=None):
        """Notify user via Slack that the AI needs help navigating.

        The AI should:
        1. Call this method to notify the user
        2. Call wait_for_user_done() to poll for "done" signal
        3. Take a new browser_snapshot to see what changed
        4. Persist any learned navigation via persist_learning()
        """
        msg = (
            f":raising_hand: *Need your help — {self.portal.portal_name}*\n\n"
            f"{reason}\n\n"
            f"Please navigate in the browser window to where I need to be, "
            f"then reply here with *done*."
        )
        if screenshot_path:
            msg += f"\n\n_Screenshot saved at: {screenshot_path}_"

        self._notify(msg)

    def wait_for_user_done(self, timeout=600, poll_interval=5):
        """Poll Slack for user's 'done' signal after a takeover request.

        Returns True if user signaled done, False if timed out.
        """
        from skills.slack.adapter import read_replies

        if not self._dm_channel or not self._user_id:
            print("[collab] No Slack config — sleeping 30s as fallback")
            time.sleep(30)
            return True

        deadline = time.time() + timeout
        check_after = str(time.time())

        while time.time() < deadline:
            time.sleep(poll_interval)
            try:
                messages = read_replies(self._dm_channel, oldest=check_after, limit=10)
                for msg in messages:
                    if msg.get("user") == self._user_id and not msg.get("bot_id"):
                        text = msg.get("text", "").lower().strip()
                        if text in ("done", "ready", "here", "ok", "go",
                                    "continue", "resume", "yes", "next"):
                            return True
            except Exception as e:
                print(f"[collab] Slack poll error: {e}")

        self._notify(f":warning: Timed out waiting for help with {self.portal.portal_name}.")
        return False

    def notify_resume(self, what_learned=None):
        """Notify user that the AI is resuming after a takeover."""
        msg = f":arrow_forward: *Resuming — {self.portal.portal_name}*"
        if what_learned:
            msg += f"\n_Learned: {what_learned}_"
        self._notify(msg)

    # ── Learning Persistence ───────────────────────────────────────

    def persist_learning(self, learning_type, description, details=None):
        """Save a navigation or interaction pattern learned during collaboration.

        Args:
            learning_type: "navigation", "credential_flow", "quirk", "selector"
            description: Human-readable description
            details: Optional dict with structured data (URLs, selectors, etc.)
        """
        LEARNINGS_DIR.mkdir(parents=True, exist_ok=True)

        portal_key = self.portal.portal_name.lower().replace(" ", "_").replace("*", "")
        learning_file = LEARNINGS_DIR / f"{portal_key}.json"

        if learning_file.exists():
            try:
                data = json.loads(learning_file.read_text())
            except (json.JSONDecodeError, IOError):
                data = {"portal": portal_key, "learnings": []}
        else:
            data = {"portal": portal_key, "learnings": []}

        entry = {
            "date": time.strftime("%Y-%m-%d"),
            "type": learning_type,
            "description": description,
        }
        if details:
            entry["details"] = details

        data["learnings"].append(entry)
        learning_file.write_text(json.dumps(data, indent=2))
        print(f"[collab] Learning persisted → {learning_file.name}: {learning_type} — {description}")

    def get_learnings(self):
        """Read all stored learnings for this portal.

        Returns list of learning dicts, or empty list.
        """
        portal_key = self.portal.portal_name.lower().replace(" ", "_").replace("*", "")
        learning_file = LEARNINGS_DIR / f"{portal_key}.json"

        if not learning_file.exists():
            return []
        try:
            data = json.loads(learning_file.read_text())
            return data.get("learnings", [])
        except (json.JSONDecodeError, IOError):
            return []

    # ── Plan Generation ────────────────────────────────────────────

    def generate_login_plan(self, login_url, has_iframe=False):
        """Generate step-by-step instructions for a collaborative login.

        Returns a list of step dicts the AI agent should execute.
        """
        interceptor = IFRAME_INTERCEPTOR_JS if has_iframe else INTERCEPTOR_JS

        return [
            {
                "step": 1,
                "action": "browser_navigate",
                "description": f"Navigate to {self.portal.portal_name} login page",
                "args": {"url": login_url},
            },
            {
                "step": 2,
                "action": "browser_wait_for",
                "description": "Wait for login page to load",
                "args": {"time": 3},
            },
            {
                "step": 3,
                "action": "browser_evaluate",
                "description": "Inject credential interceptor JS",
                "args": {"function": interceptor},
            },
            {
                "step": 4,
                "action": "slack_notify",
                "description": "Notify user via Slack to enter credentials",
                "python": "collab.notify_credential_needed(login_url)",
            },
            {
                "step": 5,
                "action": "poll_login_state",
                "description": (
                    "Poll browser every 5s using browser_evaluate with POLL_STATE_JS. "
                    "Login complete when: hasPasswordField=false OR URL changes from login URL OR "
                    "capturedCreds is not null. Timeout: 10 minutes."
                ),
                "poll_js": POLL_STATE_JS,
                "poll_interval_seconds": 5,
                "timeout_seconds": 600,
            },
            {
                "step": 6,
                "action": "browser_evaluate",
                "description": "Read captured credentials from interceptor",
                "args": {"function": READ_CREDS_JS},
            },
            {
                "step": 7,
                "action": "store_credentials",
                "description": "Store captured credentials in Keychain",
                "python": "collab.store_captured_credentials(username, password)",
            },
        ]

    # ── Internal ───────────────────────────────────────────────────

    def _notify(self, message):
        """Send a Slack DM to the user. Returns the message response or None."""
        try:
            from skills.slack.adapter import send_message
            if self._dm_channel:
                return send_message(self._dm_channel, message)
        except Exception as e:
            print(f"[collab] Slack notification failed: {e}")
        return None
