#!/usr/bin/env python3
"""Fast-polling credential capture for collaborative browser login.

Improvement over the basic `skills/browser/collaborative.INTERCEPTOR_JS` + poll
pattern: once the interceptor sees the first field (username), tighten the read
loop to every 1-2 seconds so the FINAL capture (both fields present) happens
BEFORE any page navigation can wipe sessionStorage/localStorage.

The prior approach polled every 30s. Users who entered creds then immediately
clicked Sign-In would have the partial-creds wiped by cross-origin navigation
(e.g. online.adp.com → about:blank MFA flow) before the next poll fired.
Lost capture = user has to re-enter. Annoying + preventable.

Usage pattern (AI agent drives this against user-playwright MCP):

    1. Navigate to login page.
    2. Inject MULTISTEP_INTERCEPTOR_JS via browser_evaluate.
    3. Tell user to log in.
    4. Call poll_until_captured(max_seconds=600) — this loops inside Python,
       calling browser_evaluate(POLL_AND_DRAIN_JS) every interval.
       As soon as both fields are present, it DRAINS them (sessionStorage +
       localStorage wiped in the same JS call) and returns the creds dict.
    5. Hand dict to skills.credentials.registry.add_keychain().

Drain-on-capture is critical: if we only READ but don't DELETE, the creds sit
in storage until the page navigates, which gives Square/ADP a chance to fill
them itself or echo them in an error page.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional


# JS injected once at login page load.
MULTISTEP_INTERCEPTOR_JS = r"""
(() => {
  const KEY = '__jarvis_partial_creds';
  function loadPartial() {
    try { return JSON.parse(sessionStorage.getItem(KEY) || '{}'); } catch(e) { return {}; }
  }
  function savePartial(p) {
    try { sessionStorage.setItem(KEY, JSON.stringify(p)); } catch(e) {}
    try { localStorage.setItem(KEY, JSON.stringify(p)); } catch(e) {}
  }
  function captureFromEvent(target) {
    if (!target || !target.tagName) return;
    const type = (target.type || '').toLowerCase();
    const val = target.value || '';
    if (!val) return;
    const p = loadPartial();
    if (type === 'password') p.password = val;
    else if (type === 'email' || type === 'tel' || type === 'text') {
      if (val.includes('@') || /^[+\d][\d\s\-()]{6,}$/.test(val)) p.username = val;
      else if (!p.username) p.username = val;
    }
    savePartial(p);
    if (p.username && p.password) window.__jarvis_captured_creds = p;
  }
  document.addEventListener('input', (e) => captureFromEvent(e.target), true);
  document.addEventListener('change', (e) => captureFromEvent(e.target), true);
  // Periodically rescan all inputs in case event listeners missed something
  // (React controlled components sometimes suppress native events).
  setInterval(() => {
    document.querySelectorAll('input').forEach(captureFromEvent);
  }, 500);
  return { ok: true, msg: 'multistep interceptor armed' };
})()
"""


# JS that BOTH reads the captured creds AND wipes all storage in one atomic call.
# Returning a raw JSON string lets the Python side parse it without ambiguity.
POLL_AND_DRAIN_JS = r"""
(() => {
  const KEY = '__jarvis_partial_creds';
  let p = window.__jarvis_captured_creds;
  if (!p) { try { p = JSON.parse(sessionStorage.getItem(KEY) || 'null'); } catch(e) {} }
  if (!p) { try { p = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch(e) {} }
  if (p && p.username && p.password) {
    try { sessionStorage.removeItem(KEY); } catch(e) {}
    try { localStorage.removeItem(KEY); } catch(e) {}
    window.__jarvis_captured_creds = null;
    return JSON.stringify({ complete: true, creds: p, url: window.location.href });
  }
  // Partial state — keep storage intact so the interceptor can keep building it.
  return JSON.stringify({
    complete: false,
    has_user: !!(p && p.username),
    has_pw: !!(p && p.password),
    url: window.location.href,
  });
})()
"""


@dataclass
class CaptureResult:
    username: str
    password: str
    captured_at_url: str


def poll_until_captured(
    evaluate_fn: Callable[[str], dict],
    *,
    max_seconds: int = 600,
    base_interval_seconds: float = 2.0,
    partial_interval_seconds: float = 1.0,
    idle_interval_seconds: float = 3.0,
    on_partial: Optional[Callable[[dict], None]] = None,
) -> Optional[CaptureResult]:
    """Poll the browser for credential capture completion.

    Args:
        evaluate_fn: Callable that takes a JS string and returns the parsed
            result dict (what `browser_evaluate` returns). In practice the
            AI agent wraps the MCP call and passes it in.
        max_seconds: Total time to wait before giving up (per 'never timebox
            user input' rule, default is generous — 10 minutes).
        base_interval_seconds: Poll interval when no input seen yet. 2s is a
            sweet spot: responsive but not hammering the browser.
        partial_interval_seconds: Tighter interval (1s) once username is in
            but password pending. This window is where race-with-navigation
            bit us on 2026-04-20; tightening here is the whole point.
        idle_interval_seconds: Gentler interval (3s) when page has no password
            field AND we have no username yet — user hasn't started typing.
        on_partial: Optional callback invoked with the partial state dict
            every poll. Useful for AI agent to send progress pings.

    Returns:
        CaptureResult with username, password, and URL at capture moment.
        None if max_seconds elapsed without a complete capture.
    """
    deadline = time.time() + max_seconds
    last_partial_state = None

    while time.time() < deadline:
        raw = evaluate_fn(POLL_AND_DRAIN_JS)
        # MCP returns the JS return value; depending on layer this might be a
        # string (JSON) or a dict. Normalize.
        if isinstance(raw, dict) and "result" in raw:
            raw = raw["result"]
        if isinstance(raw, str):
            import json as _json
            try:
                state = _json.loads(raw)
            except (ValueError, TypeError):
                state = {}
        elif isinstance(raw, dict):
            state = raw
        else:
            state = {}

        if state.get("complete"):
            creds = state.get("creds", {})
            return CaptureResult(
                username=creds.get("username", ""),
                password=creds.get("password", ""),
                captured_at_url=state.get("url", ""),
            )

        if on_partial:
            try:
                on_partial(state)
            except Exception:
                pass

        # Pick interval based on how close we are to a complete capture.
        if state.get("has_user"):
            interval = partial_interval_seconds      # 1s — race window
        elif state.get("has_pw"):
            interval = partial_interval_seconds
        else:
            interval = idle_interval_seconds          # 3s — user not typing yet

        if state != last_partial_state:
            last_partial_state = state
        time.sleep(interval)

    return None
