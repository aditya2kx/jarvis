"""Slack Events API webhook handler for BHAGA Cloud.

Replaces the Socket Mode listener (skills/slack/listener.py) with a stateless
HTTP POST handler. Deployed as a Cloud Run service.

Architecture:
- Slack sends event POSTs to this endpoint
- Handler verifies signing secret, parses DM messages
- OTP codes are written to Firestore for the orchestrator to poll
- Agent-aware routing from _find_pending_portal (commit 6e4f72b) is preserved

Endpoints:
  POST /slack/events  — Slack Events API
  POST /slack/commands — Slash commands (/bhaga refresh <date>, /bhaga status)
  GET  /health        — Health check for Cloud Run
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import time
from typing import Optional

from flask import Flask, Response, jsonify, request
from google.cloud import firestore

# ---------------------------------------------------------------------------
# Configuration — all from env vars (set by Cloud Run / Secret Manager)
# ---------------------------------------------------------------------------

SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
CLOCK_SKEW_TOLERANCE_S = 5 * 60  # reject timestamps older than 5 minutes

# Agent config: maps DM channel IDs → agent names.
# Loaded once at startup from AGENT_CONFIG_JSON env var, which mirrors the
# config.yaml slack.agents section:
#   {"chitra": {"dm_channel": "D0AP..."}, "bhaga": {"dm_channel": "D0AT..."}, ...}
# The orchestrator's deploy script injects this from config.yaml.
_AGENT_CONFIG: dict[str, dict] = {}
_CHANNEL_TO_AGENT: dict[str, str] = {}  # reverse lookup: channel_id → agent_name

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("webhook")

db: Optional[firestore.Client] = None

# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


def _init_agent_config() -> None:
    """Parse AGENT_CONFIG_JSON into lookup maps."""
    global _AGENT_CONFIG, _CHANNEL_TO_AGENT
    raw = os.environ.get("AGENT_CONFIG_JSON", "{}")
    try:
        _AGENT_CONFIG = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("AGENT_CONFIG_JSON is not valid JSON — agent routing disabled")
        _AGENT_CONFIG = {}

    _CHANNEL_TO_AGENT = {}
    for agent_name, cfg in _AGENT_CONFIG.items():
        ch = cfg.get("dm_channel")
        if ch:
            _CHANNEL_TO_AGENT[ch] = agent_name


def _init_firestore() -> None:
    global db
    try:
        db = firestore.Client()
        log.info("Firestore client initialized")
    except Exception as exc:
        log.error("Firestore init failed (OTP writes will be no-ops): %s", exc)
        db = None


def init_app() -> None:
    """One-time initialisation called at import time and by tests."""
    _init_agent_config()
    _init_firestore()


# Run init when gunicorn imports this module
init_app()

# ---------------------------------------------------------------------------
# Slack request verification
# ---------------------------------------------------------------------------


def verify_slack_signature(
    body: bytes,
    timestamp: str,
    signature: str,
    signing_secret: Optional[str] = None,
) -> bool:
    """Verify a Slack request signature (v0).

    Returns False if the timestamp is stale (>5 min) or the HMAC doesn't match.
    """
    secret = signing_secret or SLACK_SIGNING_SECRET
    if not secret:
        log.error("SLACK_SIGNING_SECRET is empty — cannot verify requests")
        return False

    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        return False

    if abs(time.time() - ts) > CLOCK_SKEW_TOLERANCE_S:
        return False

    sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    computed = "v0=" + hmac.new(
        secret.encode("utf-8"),
        sig_basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(computed, signature)


# ---------------------------------------------------------------------------
# OTP extraction
# ---------------------------------------------------------------------------

_OTP_PATTERNS = [
    re.compile(r"\b(\d{6})\b"),                        # bare 6-digit code
    re.compile(r"(?:code|otp|pin)\s*(?:is|:)?\s*(\d{4,8})", re.IGNORECASE),
    re.compile(r"(\d{4,8})\s*$"),                      # trailing digits
]


def extract_otp(text: str) -> Optional[str]:
    """Extract an OTP code from a message.

    Tries multiple patterns in priority order. Returns the first match that
    looks like a plausible OTP (4–8 digits), or None.
    """
    text = text.strip()
    cleaned = text.replace(" ", "").replace("-", "")
    if cleaned.isdigit() and 4 <= len(cleaned) <= 8:
        return cleaned

    for pat in _OTP_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Agent-aware OTP routing (ported from _find_pending_portal)
# ---------------------------------------------------------------------------


def _find_pending_portal_for_agent(agent: str) -> Optional[dict]:
    """Find a Firestore OTP doc with status=pending for the given agent.

    Firestore path: otps/{agent}_{portal}
    Fields: portal, agent, requested_at, status (pending/received)

    Returns the doc dict if found, else None.
    """
    if db is None:
        return None

    try:
        otps_ref = db.collection("otps")
        query = (
            otps_ref
            .where("agent", "==", agent)
            .where("status", "==", "pending")
            .order_by("requested_at")
            .limit(1)
        )
        docs = list(query.stream())
        if docs:
            data = docs[0].to_dict()
            data["_doc_id"] = docs[0].id
            return data
    except Exception as exc:
        log.error("Firestore query failed for agent=%s: %s", agent, exc)
    return None


def _complete_otp(doc_id: str, code: str) -> None:
    """Mark an OTP doc as received."""
    if db is None:
        return
    try:
        db.collection("otps").document(doc_id).update({
            "code": code,
            "received_at": firestore.SERVER_TIMESTAMP,
            "status": "received",
        })
        log.info("OTP completed: %s → %s", doc_id, code)
    except Exception as exc:
        log.error("Firestore update failed for %s: %s", doc_id, exc)


# ---------------------------------------------------------------------------
# Slash command handler (/bhaga)
# ---------------------------------------------------------------------------


def _handle_slash_command(form: dict) -> Response:
    """Handle /bhaga slash commands.

    Must respond within 3 seconds. For long-running work, return an ack
    and post follow-up via response_url.
    """
    command_text = (form.get("text") or "").strip()
    # response_url = form.get("response_url")  # for async follow-ups

    refresh_match = re.match(r"^refresh\s+(\d{4}-\d{2}-\d{2})$", command_text, re.IGNORECASE)
    if refresh_match:
        date_str = refresh_match.group(1)
        _trigger_cloud_run_job(date_str)
        return jsonify({
            "response_type": "ephemeral",
            "text": f":hourglass_flowing_sand: Refresh triggered for *{date_str}*. Check #bhaga-runs for progress.",
        })

    if command_text.lower() == "status":
        summary = _get_latest_run_summary()
        return jsonify({
            "response_type": "ephemeral",
            "text": summary,
        })

    return jsonify({
        "response_type": "ephemeral",
        "text": (
            ":robot_face: *BHAGA Commands*\n"
            "  `/bhaga refresh 2025-05-26` — trigger daily refresh for a date\n"
            "  `/bhaga status` — latest run summary"
        ),
    })


def _trigger_cloud_run_job(date_str: str) -> None:
    """Enqueue a Cloud Run Job execution for the given date.

    Uses the Cloud Run v2 API to create a job execution. The job name is
    read from CLOUD_RUN_JOB_NAME env var.
    """
    job_name = os.environ.get("CLOUD_RUN_JOB_NAME")
    if not job_name:
        log.warning("CLOUD_RUN_JOB_NAME not set — cannot trigger job")
        return

    try:
        from google.cloud import run_v2
        client = run_v2.JobsClient()
        client.run_job(
            request=run_v2.RunJobRequest(
                name=job_name,
                overrides=run_v2.RunJobRequest.Overrides(
                    container_overrides=[
                        run_v2.RunJobRequest.Overrides.ContainerOverride(
                            env=[run_v2.EnvVar(name="REFRESH_DATE", value=date_str)],
                        ),
                    ],
                ),
            ),
        )
        log.info("Cloud Run Job triggered for date=%s", date_str)
    except Exception as exc:
        log.error("Failed to trigger Cloud Run Job: %s", exc)


def _get_latest_run_summary() -> str:
    """Read the latest run summary from Firestore runs collection."""
    if db is None:
        return ":warning: Firestore not available — cannot read run status."

    try:
        runs_ref = db.collection("runs")
        query = runs_ref.order_by("started_at", direction=firestore.Query.DESCENDING).limit(1)
        docs = list(query.stream())
        if not docs:
            return ":information_source: No runs found yet."

        run = docs[0].to_dict()
        date = run.get("date", "?")
        status = run.get("status", "unknown")
        steps_done = run.get("steps_completed", [])
        steps_total = run.get("steps_total", 0)
        emoji = ":white_check_mark:" if status == "success" else ":warning:"

        return (
            f"{emoji} *Latest run:* `{date}` — {status}\n"
            f"Steps: {len(steps_done)}/{steps_total} completed"
        )
    except Exception as exc:
        log.error("Failed to read run summary: %s", exc)
        return f":x: Error reading run status: {exc}"


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/slack/events", methods=["POST"])
def slack_events():
    body = request.get_data()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not verify_slack_signature(body, timestamp, signature):
        log.warning("Invalid Slack signature — rejecting request")
        return Response("invalid signature", status=403)

    payload = request.get_json(force=True, silent=True)
    if payload is None:
        return Response("bad json", status=400)

    # URL verification challenge (Slack sends this during webhook setup)
    if payload.get("type") == "url_verification":
        return jsonify({"challenge": payload.get("challenge", "")})

    # Event callback
    if payload.get("type") == "event_callback":
        event = payload.get("event", {})
        _handle_event(event)

    return Response("ok", status=200)


@app.route("/slack/commands", methods=["POST"])
def slack_commands():
    body = request.get_data()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not verify_slack_signature(body, timestamp, signature):
        return Response("invalid signature", status=403)

    return _handle_slash_command(request.form)


# ---------------------------------------------------------------------------
# Event dispatch
# ---------------------------------------------------------------------------


def _handle_event(event: dict) -> None:
    """Process a Slack event (message in DM)."""
    if event.get("type") != "message":
        return

    # Skip bot messages and subtypes (edits, joins, etc.)
    if event.get("bot_id") or event.get("subtype"):
        return

    channel = event.get("channel", "")
    text = event.get("text", "")
    user_id = event.get("user", "")

    log.info("DM from user=%s channel=%s: %s", user_id, channel, text[:80])

    # Agent-aware routing: which agent owns this DM channel?
    agent = _CHANNEL_TO_AGENT.get(channel)
    if not agent:
        log.info("Channel %s not mapped to any agent — ignoring", channel)
        return

    otp_code = extract_otp(text)
    if not otp_code:
        log.info("No OTP found in message from channel=%s", channel)
        return

    # Find the pending OTP request for THIS agent only
    pending = _find_pending_portal_for_agent(agent)
    if not pending:
        log.info("No pending OTP request for agent=%s — ignoring code", agent)
        return

    doc_id = pending["_doc_id"]
    portal = pending.get("portal", "unknown")
    log.info(
        "OTP match: agent=%s portal=%s code=%s doc=%s",
        agent, portal, otp_code, doc_id,
    )
    _complete_otp(doc_id, otp_code)


# ---------------------------------------------------------------------------
# Local dev entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
