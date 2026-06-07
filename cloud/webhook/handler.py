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

# Run-state collections. Prod is always "runs". A live sandbox run writes its
# pending-OTP checkpoint to its own collection; the webhook scans it FIRST so a
# sandbox OTP reply resumes the sandbox job — never prod — even when both await
# OTP. Defaults to "" (DISABLED): the prod path is byte-for-byte unchanged unless
# an operator explicitly opts in by setting SANDBOX_RUNS_COLLECTION=sandbox_runs on
# the bhaga-webhook service (see RUNBOOK §13). This keeps the backward-compat
# guarantee literally true — no extra Firestore scan on a prod READY reply by
# default. (Supervised live runs don't need it anyway: BHAGA_OTP_ASSUME_READY=1
# services the OTP inline via the agent-keyed `otps` collection.)
PROD_RUNS_COLLECTION = "runs"
SANDBOX_RUNS_COLLECTION = os.environ.get("SANDBOX_RUNS_COLLECTION", "")

# Agent config: maps DM channel IDs → agent names.
# Loaded once at startup from AGENT_CONFIG_JSON env var, which mirrors the
# config.yaml slack.agents section. Build it from config.yaml via
# scripts/build_agent_config.py and inject it on the Cloud Run service with
#   gcloud run services update bhaga-webhook \
#     --update-env-vars AGENT_CONFIG_JSON="$(python3 scripts/build_agent_config.py)"
# (deploy.yml only updates the image, so an injected env var persists across
# image redeploys). Each agent entry may declare MULTIPLE DM channels:
#   {
#     "chitra": {"dm_channel": "D0AP..."},
#     "bhaga":  {"dm_channel": "D0AT...",        # local bhaga bot ↔ operator
#                "cloud_dm_channel": "D0B6..."}, # cloud bhaga-cloud bot ↔ operator
#   }
# EVERY declared channel maps to the SAME agent name, so a READY/code reply on
# either the local or the cloud bot's DM resolves to that agent's pending OTP
# run (the cloud nightly job checkpoints under agent="bhaga").
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
        if not isinstance(cfg, dict):
            continue
        # An agent may own several DM channels (e.g. a separate cloud bot).
        # Collect them from the primary `dm_channel`, an optional
        # `cloud_dm_channel`, and an optional `dm_channels` list. Map each one
        # to the same agent name so a reply on any of them routes correctly.
        channels = [cfg.get("dm_channel"), cfg.get("cloud_dm_channel")]
        channels.extend(cfg.get("dm_channels") or [])
        for ch in channels:
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


_bq: Optional[object] = None  # google.cloud.bigquery.Client | None
_BQ_PROJECT = "jarvis-bhaga-prod"
_BQ_DATASET = os.environ.get("BHAGA_BQ_DATASET", "bhaga")
_BQ_STORE_CONFIG_TABLE = f"{_BQ_PROJECT}.{_BQ_DATASET}.store_config"


def _init_bigquery() -> None:
    global _bq
    try:
        from google.cloud import bigquery  # type: ignore[import]
        _bq = bigquery.Client(project=_BQ_PROJECT)
        log.info("BigQuery client initialized")
    except Exception as exc:
        log.error("BigQuery init failed (config commands will be unavailable): %s", exc)
        _bq = None


def init_app() -> None:
    """One-time initialisation called at import time and by tests."""
    _init_agent_config()
    _init_firestore()
    _init_bigquery()


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
# READY-handshake detection
# ---------------------------------------------------------------------------
#
# Mirror of agents/bhaga/scripts/otp_gate.READY_WORDS / is_ready_reply. The
# webhook is a standalone deploy unit (its Dockerfile copies only handler.py)
# so it cannot import the skills package — keep this list in sync if you edit
# the source of truth in otp_gate.py.

READY_WORDS = {
    "ready", "ok", "okay", "go", "yes", "yep", "yup", "available", "here", "y",
}


def is_ready_reply(text: str) -> bool:
    """True if a reply means "I'm available now — send the OTP(s)"."""
    if not text:
        return False
    cleaned = str(text).strip().lower().strip("!.?*_`~ ")
    if not cleaned:
        return False
    if cleaned in READY_WORDS:
        return True
    tokens = cleaned.split()
    return bool(tokens) and tokens[0] in READY_WORDS


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
# Pending-OTP availability resume (two-step READY handshake, cloud half)
# ---------------------------------------------------------------------------
#
# When a Cloud Run job reaches an OTP gate without a prior READY it posts a
# READY request, writes a pending checkpoint into runs/<date>.pending_otp, and
# EXITS (billing stops). When the operator later replies READY, THIS webhook:
#   1. finds the pending run for that agent,
#   2. marks the checkpoint ready_received=True, and
#   3. triggers a FRESH bhaga-daily-refresh job execution for that date.
# The new execution skips done steps via markers/GCS, sees READY, triggers a
# fresh OTP, and blocks only briefly. The long wait costs nothing.
#
# The checkpoint shape mirrors skills.bhaga_config.state_adapter.save_pending_otp
# (runs/<date> doc, `pending_otp` map). Keep field names in sync.


def _scan_pending_in_collection(agent: str, collection: str) -> Optional[dict]:
    """Newest pending-OTP run for ``agent`` in one collection, or None.

    Volume is one doc/day so a full stream + client-side filter is fine and
    avoids a composite-index requirement.
    """
    candidates = []
    for doc in db.collection(collection).stream():
        data = doc.to_dict() or {}
        pending = data.get("pending_otp")
        if not pending or pending.get("ready_received"):
            continue
        if pending.get("agent", "bhaga") != agent:
            continue
        candidates.append((doc.id, pending))
    if not candidates:
        return None
    # Newest by requested_at (fall back to doc id / date string).
    candidates.sort(key=lambda c: (c[1].get("requested_at") or "", c[0]), reverse=True)
    date_id, pending = candidates[0]
    return {"date": date_id, "pending_otp": pending, "collection": collection}


def _find_pending_otp_run(agent: str) -> Optional[dict]:
    """Return {"date", "pending_otp", "collection"} for the newest run awaiting READY.

    Sandbox precedence: if SANDBOX_RUNS_COLLECTION is configured, scan it FIRST so
    a live sandbox run awaiting OTP wins over a concurrently-pending prod run —
    the operator's reply then resumes the sandbox job, not the nightly. With no
    sandbox collection configured, only prod `runs` is scanned (unchanged).
    """
    if db is None:
        return None
    try:
        if SANDBOX_RUNS_COLLECTION:
            found = _scan_pending_in_collection(agent, SANDBOX_RUNS_COLLECTION)
            if found:
                return found
        return _scan_pending_in_collection(agent, PROD_RUNS_COLLECTION)
    except Exception as exc:
        log.error("Firestore pending-OTP scan failed for agent=%s: %s", agent, exc)
        return None


def _mark_otp_ready(date_id: str, pending: dict, collection: str = PROD_RUNS_COLLECTION) -> None:
    """Set pending_otp.ready_received=True on <collection>/<date_id>."""
    if db is None:
        return
    try:
        updated = dict(pending)
        updated["ready_received"] = True
        updated["ready_at"] = firestore.SERVER_TIMESTAMP
        db.collection(collection).document(date_id).set(
            {"pending_otp": updated}, merge=True
        )
        log.info("Pending OTP marked READY for run=%s (%s)", date_id, collection)
    except Exception as exc:
        log.error("Firestore mark-ready failed for %s: %s", date_id, exc)


def _handle_ready_reply(agent: str) -> bool:
    """Resume a checkpointed run when the operator replies READY.

    Resumes whichever run the reply belongs to (sandbox has precedence): marks the
    checkpoint ready in its OWN collection and triggers the job named in the
    checkpoint's routing metadata (``target_job``), falling back to the prod
    CLOUD_RUN_JOB_NAME for a normal nightly. Returns True if a pending run was
    found and resumed; False if nothing was pending for ``agent``.
    """
    pending_run = _find_pending_otp_run(agent)
    if not pending_run:
        log.info("READY from agent=%s but no pending OTP run — ignoring", agent)
        return False
    date_id = pending_run["date"]
    pending = pending_run["pending_otp"]
    collection = pending_run.get("collection", PROD_RUNS_COLLECTION)
    _mark_otp_ready(date_id, pending, collection)
    target_job = pending.get("target_job")
    log.info(
        "READY from agent=%s → resuming %s run for date=%s (job=%s)",
        agent, collection, date_id, target_job or "<prod default>",
    )
    # Preserve the single-arg prod call (and its tests) when there's no routing.
    if target_job:
        _trigger_cloud_run_job(date_id, job_name=target_job)
    else:
        _trigger_cloud_run_job(date_id)
    return True


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

    # config get <key>
    config_get_match = re.match(r"^config\s+get\s+(\S+)$", command_text, re.IGNORECASE)
    if config_get_match:
        key = config_get_match.group(1)
        return _handle_config_get(key, form)

    # config set <key> <value>
    config_set_match = re.match(r"^config\s+set\s+(\S+)\s+(.+)$", command_text, re.IGNORECASE)
    if config_set_match:
        key = config_set_match.group(1)
        value = config_set_match.group(2).strip()
        return _handle_config_set(key, value, form)

    return jsonify({
        "response_type": "ephemeral",
        "text": (
            ":robot_face: *BHAGA Commands*\n"
            "  `/bhaga-cloud refresh 2025-05-26` — trigger daily refresh for a date\n"
            "  `/bhaga-cloud status` — latest run summary\n"
            "  `/bhaga-cloud config get <key>` — read a store config tunable\n"
            "  `/bhaga-cloud config set <key> <value>` — update a store config tunable"
        ),
    })


def _handle_config_get(key: str, form: dict) -> Response:
    """Return the current value of a store config key from BQ."""
    if _bq is None:
        return jsonify({
            "response_type": "ephemeral",
            "text": ":warning: BigQuery not available — config commands are unavailable.",
        })
    store = os.environ.get("BHAGA_STORE", "palmetto")
    try:
        rows = list(_bq.query(  # type: ignore[union-attr]
            f"SELECT value, updated_by, CAST(updated_at AS STRING) AS updated_at"
            f" FROM `{_BQ_STORE_CONFIG_TABLE}`"
            f" WHERE store = @store AND key = @key"
            f" ORDER BY updated_at DESC LIMIT 1",
            job_config=_bq_param_config([("store", "STRING", store), ("key", "STRING", key)]),
        ).result())
        if not rows:
            return jsonify({
                "response_type": "ephemeral",
                "text": f":information_source: `{key}` is not set in `{store}` config.",
            })
        row = dict(rows[0])
        return jsonify({
            "response_type": "ephemeral",
            "text": (
                f":white_check_mark: `{key}` = *{row['value']}*"
                + (f"  _(set by {row['updated_by']} at {row['updated_at']})_" if row.get("updated_by") else "")
            ),
        })
    except Exception as exc:
        log.error("config get failed: %s", exc)
        return jsonify({
            "response_type": "ephemeral",
            "text": f":x: config get failed: {exc}",
        })


def _handle_config_set(key: str, value: str, form: dict) -> Response:
    """Upsert a store config key in BQ."""
    if _bq is None:
        return jsonify({
            "response_type": "ephemeral",
            "text": ":warning: BigQuery not available — config commands are unavailable.",
        })
    store = os.environ.get("BHAGA_STORE", "palmetto")
    user_name = form.get("user_name") or form.get("user_id") or "slack"
    try:
        from google.cloud import bigquery as _bq_mod  # type: ignore[import]
        fq = f"`{_BQ_STORE_CONFIG_TABLE}`"
        _bq.query(  # type: ignore[union-attr]
            f"MERGE {fq} T"
            f" USING (SELECT @store AS store, @key AS key) S"
            f" ON T.store = S.store AND T.key = S.key"
            f" WHEN MATCHED THEN UPDATE SET value = @value, updated_at = CURRENT_TIMESTAMP(), updated_by = @by"
            f" WHEN NOT MATCHED THEN INSERT (store, key, value, updated_at, updated_by)"
            f"   VALUES (@store, @key, @value, CURRENT_TIMESTAMP(), @by)",
            job_config=_bq_param_config([
                ("store", "STRING", store),
                ("key", "STRING", key),
                ("value", "STRING", value),
                ("by", "STRING", user_name),
            ]),
        ).result()
        return jsonify({
            "response_type": "ephemeral",
            "text": f":white_check_mark: `{key}` set to *{value}* (by {user_name})",
        })
    except Exception as exc:
        log.error("config set failed: %s", exc)
        return jsonify({
            "response_type": "ephemeral",
            "text": f":x: config set failed: {exc}",
        })


def _bq_param_config(params: list[tuple]) -> object:
    """Build a BigQuery QueryJobConfig with scalar parameters."""
    from google.cloud import bigquery as _bq_mod  # type: ignore[import]
    return _bq_mod.QueryJobConfig(query_parameters=[
        _bq_mod.ScalarQueryParameter(name, typ, val)
        for name, typ, val in params
    ])


def _trigger_cloud_run_job(date_str: str, job_name: Optional[str] = None) -> None:
    """Enqueue a Cloud Run Job execution for the given date.

    Uses the Cloud Run v2 API to create a job execution. ``job_name`` defaults to
    the prod CLOUD_RUN_JOB_NAME env var; a sandbox OTP resume passes the sandbox
    job's resource name so the reply runs the sandbox job, not prod.
    """
    job_name = job_name or os.environ.get("CLOUD_RUN_JOB_NAME")
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

    # Two-step OTP handshake: a READY reply resumes a checkpointed run by
    # triggering a fresh job execution. Check this BEFORE OTP extraction so a
    # word like "ready"/"go" is never misread, and so a READY reply that
    # arrives while no code is pending still resumes the run.
    if is_ready_reply(text):
        if _handle_ready_reply(agent):
            return
        # No pending run to resume — fall through (nothing else to do for a
        # bare READY word; extract_otp will return None below).

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
