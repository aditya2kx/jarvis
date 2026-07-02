"""Slack Events API webhook handler for BHAGA Cloud.

Replaces the Socket Mode listener (skills/slack/listener.py) with a stateless
HTTP POST handler. Deployed as a Cloud Run service.

Architecture:
- Slack sends event POSTs to this endpoint
- Handler verifies signing secret, parses DM messages
- OTP codes are written to Firestore for the orchestrator to poll
- Agent-aware routing from _find_pending_portal (commit 6e4f72b) is preserved

Slash command ack strategy (3s deadline):
- Every /bhaga-cloud command returns a generic ephemeral ack immediately after
  pure parsing, well within Slack's 3s deadline.  No BQ / Cloud Run / Firestore
  I/O runs in the synchronous ack path.
- All real work (BQ coverage probes, run_v2 triggers, Firestore reads/writes)
  runs in a background daemon thread dispatched BEFORE the ack is returned.
- The worker posts the real result back to Slack via the `response_url` that
  Slack includes in every slash-command payload (valid ~30 min, up to 5 posts,
  no bot token required):
    - refresh  → response_type "in_channel" (visible to channel members when
                 run in a shared channel like #bhaga-runs)
    - all others → response_type "ephemeral" (operator-private, matches today's
                 visibility for config/training/alias/exclude/status)
- Parse errors (bad date, over-cap, unknown token) remain synchronous so the
  operator gets an inline :x: immediately.

Endpoints:
  POST /slack/events       — Slack Events API
  POST /slack/commands     — Slash commands (/bhaga-cloud refresh, status, config, restock, …)
  POST /slack/interactions — Slack interactivity (restock modal view_submission, Issue #137)
  GET  /health             — Health check for Cloud Run
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import logging
import os
import re
import threading
import time
import urllib.request
from typing import Callable, Optional

from flask import Flask, Response, jsonify, request
from google.cloud import firestore

# ---------------------------------------------------------------------------
# Configuration — all from env vars (set by Cloud Run / Secret Manager)
# ---------------------------------------------------------------------------

SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
CLOCK_SKEW_TOLERANCE_S = 5 * 60  # reject timestamps older than 5 minutes

# Direct sandbox trigger — bypasses Slack HMAC verification when
# X-Sandbox-Trigger: <token> matches this secret. The bypass path ALWAYS routes
# to the sandbox job (bhaga-sandbox-refresh + bhaga_sandbox), never prod.
# Fail-closed: when empty, no bypass is possible and the header is ignored.
_SANDBOX_TRIGGER_TOKEN = os.environ.get("SANDBOX_TRIGGER_TOKEN", "")
_SANDBOX_JOB_RESOURCE = os.environ.get(
    "BHAGA_SANDBOX_JOB_NAME",
    "projects/jarvis-bhaga-prod/locations/us-central1/jobs/bhaga-sandbox-refresh",
)
_SANDBOX_BQ_DATASET = "bhaga_sandbox"

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
_BQ_TRAINING_SHIFTS_TABLE = f"{_BQ_PROJECT}.{_BQ_DATASET}.training_shifts"
_BQ_EMPLOYEE_ALIASES_TABLE = f"{_BQ_PROJECT}.{_BQ_DATASET}.employee_aliases"
_BQ_RESTOCK_SCHEDULE_TABLE = f"{_BQ_PROJECT}.{_BQ_DATASET}.inventory_restock_schedule"
_BQ_RESTOCK_ORDERS_TABLE = f"{_BQ_PROJECT}.{_BQ_DATASET}.inventory_restock_orders"

# Bot token for Slack Web API calls (views.open, files download, chat.postMessage).
# Unlike response_url (used by every other command), the restock modal needs a
# real bot token because views.open must be called BEFORE any response_url
# exists (there's no slash-command response_url yet at modal-open time).
# Secret: slack-bot-token (already mounted on bhaga-webhook — see RUNBOOK.md
# § bhaga-webhook environment; this is the "bhaga cloud" bot's token).
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")

# Copied from skills/inventory_parse/parse.py ACTIVE_BASES. handler.py is a
# standalone deploy unit (Dockerfile copies only this file) so it cannot
# import skills/ — duplicated here deliberately, not a shared import.
_ACTIVE_BASES = ("Açaí", "Coconut", "Tropical", "Mango", "Pitaya", "Matcha", "Ube", "Pog")


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
# Multi-date refresh: parser, coverage probe, and env-override builder
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_REFRESH_DATES = 31  # guard against typos like 2026-01-01..2026-12-31


def _parse_refresh_dates(text: str) -> tuple[list[str], Optional[str]]:
    """Parse the argument portion of a 'refresh ...' command into a sorted, deduped date list.

    Accepted forms (comma-separated items; each item is a date or a range):
      2026-06-14
      2026-06-14,2026-06-15          (comma list, spaces optional)
      2026-06-14 2026-06-15          (space-separated dates only — no 'to' keyword)
      2026-06-14..2026-06-20         (inclusive range, double-dot)
      2026-06-14 to 2026-06-20       (inclusive range, 'to' keyword)
      2026-06-14,2026-06-20..2026-06-22,2026-06-25  (mixed)

    Returns (dates, None) on success; ([], error_message) on any parse failure.
    Pure — no I/O.
    """
    import datetime as _dt

    raw = text.strip()
    if not raw:
        return [], "no dates provided"

    # Normalise: treat 'YYYY-MM-DD to YYYY-MM-DD' ranges by replacing ' to ' with '..'
    # so the tokeniser only sees two forms: bare dates and 'start..end' ranges.
    # We do this carefully: only replace ' to ' when surrounded by date-shaped tokens.
    # Strategy: split on commas first, then within each comma-token detect 'to'.
    def _expand_item(item: str) -> tuple[list[str], Optional[str]]:
        item = item.strip()
        # 'YYYY-MM-DD to YYYY-MM-DD'
        to_match = re.match(
            r"^(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})$", item, re.IGNORECASE
        )
        if to_match:
            start_s, end_s = to_match.group(1), to_match.group(2)
            return _expand_range(start_s, end_s)
        # 'YYYY-MM-DD..YYYY-MM-DD'
        dotdot_match = re.match(r"^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$", item)
        if dotdot_match:
            start_s, end_s = dotdot_match.group(1), dotdot_match.group(2)
            return _expand_range(start_s, end_s)
        # bare date
        if _DATE_RE.match(item):
            try:
                _dt.date.fromisoformat(item)
                return [item], None
            except ValueError:
                return [], f"invalid date {item!r}"
        return [], f"unrecognised token {item!r} (expected YYYY-MM-DD, YYYY-MM-DD..YYYY-MM-DD, or YYYY-MM-DD to YYYY-MM-DD)"

    def _expand_range(start_s: str, end_s: str) -> tuple[list[str], Optional[str]]:
        try:
            start = _dt.date.fromisoformat(start_s)
            end = _dt.date.fromisoformat(end_s)
        except ValueError as exc:
            return [], f"invalid date in range: {exc}"
        if start > end:
            return [], f"range start {start_s} is after end {end_s}"
        days = (end - start).days + 1
        return [
            (start + _dt.timedelta(days=i)).isoformat() for i in range(days)
        ], None

    # Tokenise: if no comma present AND no '..' and no ' to ' keyword, allow space-sep dates.
    # If a comma is present, split on commas (each token may be a range).
    if "," in raw or ".." in raw or re.search(r"\bto\b", raw, re.IGNORECASE):
        # Comma-split; each token can be a date or a range.
        # ' to ' ranges are comma-delimited items themselves, so rejoin tokens.
        tokens = [t.strip() for t in raw.split(",") if t.strip()]
        # Re-join adjacent tokens connected by ' to ' that were split by comma
        # (shouldn't happen in normal usage but be safe).
        merged: list[str] = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            # Peek: if this token ends with a date and the next starts with 'to'
            if (
                i + 2 < len(tokens)
                and _DATE_RE.match(tok)
                and tokens[i + 1].lower() == "to"
                and _DATE_RE.match(tokens[i + 2])
            ):
                merged.append(f"{tok} to {tokens[i + 2]}")
                i += 3
            else:
                merged.append(tok)
                i += 1
        tokens = merged
    else:
        # Space-separated list of bare dates (no range syntax).
        tokens = raw.split()

    collected: list[str] = []
    for tok in tokens:
        dates_for_tok, err = _expand_item(tok)
        if err:
            return [], err
        collected.extend(dates_for_tok)

    # Dedup + sort ascending.
    deduped = sorted(set(collected))
    if len(deduped) > _MAX_REFRESH_DATES:
        return [], (
            f"too many dates resolved ({len(deduped)}) — cap is {_MAX_REFRESH_DATES}. "
            f"Split into smaller batches."
        )
    return deduped, None


def _date_is_covered(date_str: str, dataset: Optional[str] = None) -> bool:
    """True if raw Square data already covers date_str in BigQuery.

    ``dataset`` defaults to the module-level ``_BQ_DATASET`` (prod ``bhaga``).
    Pass ``dataset="bhaga_sandbox"`` for the sandbox bypass path to avoid
    mutating module state — the probe then reads the sandbox dataset, which is
    empty for new dates → full scrape + OTP.

    Mirrors scripts/trigger_dated_refresh.py::_date_is_covered.
    Fail-open: returns False (→ full scrape) on any error so a BQ outage
    never silently skips a date the operator asked to refresh.
    """
    if _bq is None:
        return False
    import datetime as _dt
    ds = dataset or _BQ_DATASET
    try:
        sql = (
            f"SELECT MAX(date_local) AS m"
            f" FROM `{_BQ_PROJECT}.{ds}.square_daily_rollup`"
        )
        rows = list(_bq.query(sql).result())  # type: ignore[union-attr]
        max_date = rows[0]["m"] if rows else None
        if max_date is None:
            return False
        return _dt.date.fromisoformat(date_str) <= max_date
    except Exception as exc:
        log.warning("BQ coverage probe failed for %s (fail-open → full scrape): %s", date_str, exc)
        return False


def _decide_recompute(date_str: str, dataset: Optional[str] = None) -> bool:
    """True when the date is already covered in BQ → recompute-only (no scrape, no OTP)."""
    return _date_is_covered(date_str, dataset=dataset)


def _build_refresh_env_overrides(date_str: str, recompute_only: bool) -> list[tuple[str, str]]:
    """Return the per-execution env overrides as (name, value) tuples.

    Mirrors scripts/trigger_dated_refresh.py::_build_env_overrides.
    Both modes add BHAGA_IGNORE_HALT=1 (operator-driven backfill includes the fix).
    Full-scrape dates start inline (no READY handshake) under the default gate
    mode; BHAGA_OTP_FORCE_REQUEST is no longer injected here.
    """
    env = [("REFRESH_DATE", date_str)]
    if recompute_only:
        env += [
            ("BHAGA_SKIP_SQUARE", "1"),
            ("BHAGA_SKIP_ADP", "1"),
            ("BHAGA_SKIP_KDS", "1"),
        ]
    env.append(("BHAGA_IGNORE_HALT", "1"))
    return env


# ---------------------------------------------------------------------------
# Async ack helpers
# ---------------------------------------------------------------------------
# handler.py is a standalone deploy unit (its Dockerfile copies only this file)
# so it cannot import skills.slack.adapter.  All Slack I/O here uses stdlib
# urllib.request directly.


def _post_response_url(response_url: str, payload: dict) -> None:
    """POST a follow-up payload to Slack's response_url (best-effort).

    Slack's response_url is included in every slash-command payload, accepts
    up to 5 follow-up posts within ~30 minutes, and requires no bot token.
    Used by async workers to deliver the real command result after the 3s ack.

    Fails silently: logs a greppable breadcrumb but never raises, so a dropped
    follow-up never blocks the work that already ran in the worker thread.
    """
    if not response_url:
        log.warning("_post_response_url: empty response_url — cannot deliver follow-up")
        return
    try:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            response_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            if status != 200:
                log.warning("_post_response_url: Slack returned HTTP %s", status)
    except Exception as exc:
        log.error("_post_response_url failed (breadcrumb): %s", exc)


def _dispatch_async(fn: Callable, *args) -> None:
    """Spawn a daemon thread to run fn(*args) after the HTTP ack has returned.

    Daemon=True so the thread does not prevent Cloud Run container shutdown.
    Injectable for tests: monkeypatch _dispatch_async to lambda fn, *a: fn(*a)
    to run the worker synchronously and inspect its side effects.
    """
    t = threading.Thread(target=fn, args=args, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# Slash command handler (/bhaga)
# ---------------------------------------------------------------------------


def _run_refresh_worker(
    dates: list,
    sandbox: bool,
    response_url: str,
) -> None:
    """Background worker: BQ coverage probe + Cloud Run triggers + response_url follow-up.

    Runs in a daemon thread after the 3s ack has been returned. Posts the real
    per-date mode-label summary back to the operator via response_url so they
    see the actual per-date plan (full+OTP / recompute) rather than silence.
    """
    probe_dataset = _SANDBOX_BQ_DATASET if sandbox else None
    job_name = _SANDBOX_JOB_RESOURCE if sandbox else None
    prefix = ":test_tube: [SANDBOX] " if sandbox else ":hourglass_flowing_sand: "
    date_labels: list[str] = []
    for date_str in dates:
        recompute_only = _decide_recompute(date_str, dataset=probe_dataset)
        mode_label = "recompute" if recompute_only else "full+OTP"
        env_overrides = _build_refresh_env_overrides(date_str, recompute_only)
        _trigger_cloud_run_job_with_env(date_str, env_overrides, job_name=job_name)
        date_labels.append(f"{date_str} ({mode_label})")
    dates_text = ", ".join(date_labels)
    summary = f"{prefix}Refresh triggered: {dates_text}."
    _post_response_url(response_url, {"response_type": "in_channel", "text": summary})


def _handle_slash_command(form: dict, sandbox: bool = False) -> Response:
    """Handle /bhaga-cloud slash commands.

    Returns an immediate generic ack within Slack's 3s deadline.  All BQ /
    Cloud Run / Firestore I/O runs in a background thread dispatched BEFORE
    the ack is returned; the real result is posted back via response_url.

    Parse errors (bad date, over-cap, unknown token) are synchronous — the
    operator gets an inline :x: immediately without a follow-up.

    When ``sandbox=True`` (set by the direct-trigger bypass path), the refresh
    targets ``bhaga-sandbox-refresh`` + ``bhaga_sandbox`` dataset and prefixes
    all ack/result text with :test_tube:[SANDBOX]. The bypass path can never
    affect prod: job name and BQ dataset are fixed to sandbox values per-call.
    """
    command_text = (form.get("text") or "").strip()
    response_url = form.get("response_url", "")

    refresh_match = re.match(r"^refresh\s+(.+)$", command_text, re.IGNORECASE)
    if refresh_match:
        dates, parse_err = _parse_refresh_dates(refresh_match.group(1))
        if parse_err:
            return jsonify({
                "response_type": "ephemeral",
                "text": f":x: refresh parse error: {parse_err}",
            })
        prefix = ":test_tube: [SANDBOX] " if sandbox else ":hourglass_flowing_sand: "
        _dispatch_async(_run_refresh_worker, dates, sandbox, response_url)
        return jsonify({
            "response_type": "ephemeral",
            "text": (
                f"{prefix}Refresh queued for {len(dates)} date(s) — "
                f"probing coverage + triggering; per-date summary to follow."
            ),
        })

    if command_text.lower() == "status":
        _dispatch_async(_get_latest_run_summary_and_post, response_url)
        return jsonify({
            "response_type": "ephemeral",
            "text": ":hourglass_flowing_sand: status queued — posting summary shortly.",
        })

    # config get <key>
    config_get_match = re.match(r"^config\s+get\s+(\S+)$", command_text, re.IGNORECASE)
    if config_get_match:
        key = config_get_match.group(1)
        _dispatch_async(_handle_config_get, key, form, response_url)
        return jsonify({
            "response_type": "ephemeral",
            "text": f":hourglass_flowing_sand: config get `{key}` — posting result shortly.",
        })

    # config set <key> <value>
    config_set_match = re.match(r"^config\s+set\s+(\S+)\s+(.+)$", command_text, re.IGNORECASE)
    if config_set_match:
        key = config_set_match.group(1)
        value = config_set_match.group(2).strip()
        _dispatch_async(_handle_config_set, key, value, form, response_url)
        return jsonify({
            "response_type": "ephemeral",
            "text": f":hourglass_flowing_sand: config set `{key}` — posting result shortly.",
        })

    # training set "Last, First" YYYY-MM-DD [note]
    training_set_match = re.match(
        r'^training\s+set\s+"([^"]+)"\s+(\d{4}-\d{2}-\d{2})(?:\s+(.*))?$',
        command_text, re.IGNORECASE,
    )
    if training_set_match:
        name = training_set_match.group(1).strip()
        date_str = training_set_match.group(2)
        note = (training_set_match.group(3) or "").strip()
        _dispatch_async(_handle_training_set, name, date_str, note, form, response_url)
        return jsonify({
            "response_type": "ephemeral",
            "text": ":hourglass_flowing_sand: training set — posting result shortly.",
        })

    # training rm "Last, First" YYYY-MM-DD
    training_rm_match = re.match(
        r'^training\s+rm\s+"([^"]+)"\s+(\d{4}-\d{2}-\d{2})$',
        command_text, re.IGNORECASE,
    )
    if training_rm_match:
        name = training_rm_match.group(1).strip()
        date_str = training_rm_match.group(2)
        _dispatch_async(_handle_training_rm, name, date_str, form, response_url)
        return jsonify({
            "response_type": "ephemeral",
            "text": ":hourglass_flowing_sand: training rm — posting result shortly.",
        })

    # alias set <raw_or_"raw name"> "Last, First"
    alias_set_match = re.match(
        r'^alias\s+set\s+(?:"([^"]+)"|(\S+))\s+"([^"]+)"$',
        command_text, re.IGNORECASE,
    )
    if alias_set_match:
        raw_name = (alias_set_match.group(1) or alias_set_match.group(2)).strip()
        canonical = alias_set_match.group(3).strip()
        _dispatch_async(_handle_alias_set, raw_name, canonical, form, response_url)
        return jsonify({
            "response_type": "ephemeral",
            "text": ":hourglass_flowing_sand: alias set — posting result shortly.",
        })

    # exclude set "Last, First" [YYYY-MM-DD]
    exclude_set_match = re.match(
        r'^exclude\s+set\s+"([^"]+)"(?:\s+(\d{4}-\d{2}-\d{2}))?$',
        command_text, re.IGNORECASE,
    )
    if exclude_set_match:
        name = exclude_set_match.group(1).strip()
        through_date = (exclude_set_match.group(2) or "").strip()
        _dispatch_async(_handle_exclude_set, name, through_date, form, response_url)
        return jsonify({
            "response_type": "ephemeral",
            "text": ":hourglass_flowing_sand: exclude set — posting result shortly.",
        })

    # restock — opens a modal (add/reset restock date + CSV upload); the modal
    # itself is opened synchronously via views.open (fast Slack Web API call,
    # well within the 3s ack deadline) — there is no response_url-deferred
    # path here because the real work happens on view_submission, handled by
    # the separate /slack/interactions route.
    if command_text.lower() == "restock":
        trigger_id = form.get("trigger_id", "")
        error = _open_restock_modal(trigger_id)
        if error:
            return jsonify({"response_type": "ephemeral", "text": f":x: {error}"})
        return Response("", status=200)

    return jsonify({
        "response_type": "ephemeral",
        "text": (
            ":robot_face: *BHAGA Commands*\n"
            "  `/bhaga-cloud refresh YYYY-MM-DD` — trigger daily refresh for a single date\n"
            "  `/bhaga-cloud refresh YYYY-MM-DD,YYYY-MM-DD` — comma/space list of dates\n"
            "  `/bhaga-cloud refresh YYYY-MM-DD..YYYY-MM-DD` — inclusive date range (.. or 'to')\n"
            "  BQ-covered dates → recompute-only (no OTP); uncovered → full scrape + OTP.\n"
            "  `/bhaga-cloud status` — latest run summary\n"
            "  `/bhaga-cloud config get <key>` — read a store config tunable\n"
            "  `/bhaga-cloud config set <key> <value>` — update a store config tunable\n"
            "  `/bhaga-cloud training set \"Last, First\" YYYY-MM-DD [note]` — mark a shift as training\n"
            "  `/bhaga-cloud training rm \"Last, First\" YYYY-MM-DD` — remove a training shift mark\n"
            "  `/bhaga-cloud alias set <raw_name> \"Last, First\"` — add employee alias\n"
            "  `/bhaga-cloud exclude set \"Last, First\" [YYYY-MM-DD]` — mark training exclusion through date\n"
            "  `/bhaga-cloud restock` — open a modal to register a restock delivery date and "
            "upload/reset its actual order CSV (base,quantity)\n"
            "  :test_tube: *Direct sandbox trigger* — POST to /slack/commands with "
            "`X-Sandbox-Trigger: <token>` header (always routes to bhaga-sandbox-refresh, never prod)"
        ),
    })


def _get_latest_run_summary_and_post(response_url: str) -> None:
    """Background worker: fetch latest run summary and post via response_url."""
    summary = _get_latest_run_summary()
    _post_response_url(response_url, {"response_type": "ephemeral", "text": summary})


def _handle_config_get(key: str, form: dict, response_url: str = "") -> None:
    """Fetch the current value of a store config key from BQ and post the result."""
    if _bq is None:
        _post_response_url(response_url, {
            "response_type": "ephemeral",
            "text": ":warning: BigQuery not available — config commands are unavailable.",
        })
        return
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
            _post_response_url(response_url, {
                "response_type": "ephemeral",
                "text": f":information_source: `{key}` is not set in `{store}` config.",
            })
            return
        row = dict(rows[0])
        _post_response_url(response_url, {
            "response_type": "ephemeral",
            "text": (
                f":white_check_mark: `{key}` = *{row['value']}*"
                + (f"  _(set by {row['updated_by']} at {row['updated_at']})_" if row.get("updated_by") else "")
            ),
        })
    except Exception as exc:
        log.error("config get failed: %s", exc)
        _post_response_url(response_url, {
            "response_type": "ephemeral",
            "text": f":x: config get failed: {exc}",
        })


def _handle_config_set(key: str, value: str, form: dict, response_url: str = "") -> None:
    """Upsert a store config key in BQ and post the result."""
    if _bq is None:
        _post_response_url(response_url, {
            "response_type": "ephemeral",
            "text": ":warning: BigQuery not available — config commands are unavailable.",
        })
        return
    store = os.environ.get("BHAGA_STORE", "palmetto")
    user_name = form.get("user_name") or form.get("user_id") or "slack"
    try:
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
        _post_response_url(response_url, {
            "response_type": "ephemeral",
            "text": f":white_check_mark: `{key}` set to *{value}* (by {user_name})",
        })
    except Exception as exc:
        log.error("config set failed: %s", exc)
        _post_response_url(response_url, {
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


def _handle_training_set(name: str, date_str: str, note: str, form: dict, response_url: str = "") -> None:
    """MERGE a per-shift training mark into BQ training_shifts and post the result."""
    if _bq is None:
        _post_response_url(response_url, {"response_type": "ephemeral",
                                          "text": ":warning: BigQuery not available."})
        return
    store = os.environ.get("BHAGA_STORE", "palmetto")
    user_name = form.get("user_name") or form.get("user_id") or "slack"
    try:
        from agents.bhaga.scripts.model_inputs import normalize_input_name  # noqa: PLC0415
        name = normalize_input_name(store, name)
    except ValueError as exc:
        _post_response_url(response_url, {"response_type": "ephemeral",
                                          "text": f":x: {exc}"})
        return
    try:
        fq = f"`{_BQ_TRAINING_SHIFTS_TABLE}`"
        _bq.query(  # type: ignore[union-attr]
            f"MERGE {fq} T"
            f" USING (SELECT @store AS store, @name AS employee_name, @date AS date) S"
            f" ON T.store=S.store AND T.employee_name=S.employee_name AND T.date=S.date"
            f" WHEN MATCHED THEN UPDATE SET note=@note, updated_at=CURRENT_TIMESTAMP(), updated_by=@by"
            f" WHEN NOT MATCHED THEN INSERT (store,employee_name,date,note,updated_at,updated_by)"
            f"   VALUES (@store,@name,@date,@note,CURRENT_TIMESTAMP(),@by)",
            job_config=_bq_param_config([
                ("store", "STRING", store),
                ("name", "STRING", name),
                ("date", "DATE", date_str),
                ("note", "STRING", note),
                ("by", "STRING", user_name),
            ]),
        ).result()
        _post_response_url(response_url, {
            "response_type": "ephemeral",
            "text": f":white_check_mark: Training shift set: *{name}* on {date_str}" +
                    (f" ({note})" if note else "") + f" (by {user_name})",
        })
    except Exception as exc:
        log.error("training set failed: %s", exc)
        _post_response_url(response_url, {"response_type": "ephemeral",
                                          "text": f":x: training set failed: {exc}"})


def _handle_training_rm(name: str, date_str: str, form: dict, response_url: str = "") -> None:
    """Delete a per-shift training mark from BQ training_shifts and post the result."""
    if _bq is None:
        _post_response_url(response_url, {"response_type": "ephemeral",
                                          "text": ":warning: BigQuery not available."})
        return
    store = os.environ.get("BHAGA_STORE", "palmetto")
    user_name = form.get("user_name") or form.get("user_id") or "slack"
    try:
        fq = f"`{_BQ_TRAINING_SHIFTS_TABLE}`"
        _bq.query(  # type: ignore[union-attr]
            f"DELETE FROM {fq} WHERE store=@store AND employee_name=@name AND date=@date",
            job_config=_bq_param_config([
                ("store", "STRING", store),
                ("name", "STRING", name),
                ("date", "DATE", date_str),
            ]),
        ).result()
        _post_response_url(response_url, {
            "response_type": "ephemeral",
            "text": f":white_check_mark: Training shift removed: *{name}* on {date_str} (by {user_name})",
        })
    except Exception as exc:
        log.error("training rm failed: %s", exc)
        _post_response_url(response_url, {"response_type": "ephemeral",
                                          "text": f":x: training rm failed: {exc}"})


def _handle_alias_set(raw_name: str, canonical: str, form: dict, response_url: str = "") -> None:
    """MERGE a new employee alias into BQ employee_aliases and post the result."""
    if _bq is None:
        _post_response_url(response_url, {"response_type": "ephemeral",
                                          "text": ":warning: BigQuery not available."})
        return
    store = os.environ.get("BHAGA_STORE", "palmetto")
    user_name = form.get("user_name") or form.get("user_id") or "slack"
    try:
        fq = f"`{_BQ_EMPLOYEE_ALIASES_TABLE}`"
        _bq.query(  # type: ignore[union-attr]
            f"MERGE {fq} T"
            f" USING (SELECT @store AS store, @raw AS raw_name) S"
            f" ON T.store=S.store AND T.raw_name=S.raw_name"
            f" WHEN MATCHED THEN UPDATE SET canonical_name=@canonical, updated_at=CURRENT_TIMESTAMP(), updated_by=@by"
            f" WHEN NOT MATCHED THEN INSERT (store,raw_name,canonical_name,notes,updated_at,updated_by)"
            f"   VALUES (@store,@raw,@canonical,'',CURRENT_TIMESTAMP(),@by)",
            job_config=_bq_param_config([
                ("store", "STRING", store),
                ("raw", "STRING", raw_name),
                ("canonical", "STRING", canonical),
                ("by", "STRING", user_name),
            ]),
        ).result()
        _post_response_url(response_url, {
            "response_type": "ephemeral",
            "text": f":white_check_mark: Alias set: `{raw_name}` → *{canonical}* (by {user_name})",
        })
    except Exception as exc:
        log.error("alias set failed: %s", exc)
        _post_response_url(response_url, {"response_type": "ephemeral",
                                          "text": f":x: alias set failed: {exc}"})


def _handle_exclude_set(name: str, through_date: str, form: dict, response_url: str = "") -> None:
    """Set a training_excluded:<name> entry in store_config BQ and post the result.

    If through_date is provided, sets training_excluded:<name>=<date>.
    If empty, appends name to excluded_from_tip_pool (permanent exclusion).
    """
    if _bq is None:
        _post_response_url(response_url, {"response_type": "ephemeral",
                                          "text": ":warning: BigQuery not available."})
        return
    store = os.environ.get("BHAGA_STORE", "palmetto")
    try:
        from agents.bhaga.scripts.model_inputs import normalize_input_name  # noqa: PLC0415
        name = normalize_input_name(store, name)
    except ValueError as exc:
        _post_response_url(response_url, {"response_type": "ephemeral",
                                          "text": f":x: {exc}"})
        return

    if through_date:
        key = f"training_excluded:{name}"
        _handle_config_set(key, through_date, form, response_url)
        return

    # Permanent exclusion: append to excluded_from_tip_pool
    try:
        fq_cfg = f"`{_BQ_STORE_CONFIG_TABLE}`"
        rows = list(_bq.query(  # type: ignore[union-attr]
            f"SELECT value FROM {fq_cfg} WHERE store=@store AND key='excluded_from_tip_pool' LIMIT 1",
            job_config=_bq_param_config([("store", "STRING", store)]),
        ).result())
        existing = rows[0]["value"] if rows else ""
        names = [n.strip() for n in existing.split(";") if n.strip()] if existing else []
        if name not in names:
            names.append(name)
        new_value = ";".join(names)
        _handle_config_set("excluded_from_tip_pool", new_value, form, response_url)
    except Exception as exc:
        log.error("exclude set failed: %s", exc)
        _post_response_url(response_url, {"response_type": "ephemeral",
                                          "text": f":x: exclude set failed: {exc}"})


# ---------------------------------------------------------------------------
# Restock modal (Issue #137) — register a restock delivery date and
# optionally upload/reset the actual order CSV for it.
# ---------------------------------------------------------------------------

_RESTOCK_CALLBACK_ID = "restock_submit"
_RESTOCK_ACTIONS = ("Add order (actuals)", "Register date only (estimated)", "Reset to estimated")


def _slack_api(method: str, payload: dict) -> dict:
    """POST JSON to https://slack.com/api/<method> with the bot token.

    Returns the parsed JSON response (may contain ok=False + an 'error' key
    on failure — callers check 'ok' explicitly). Raises on transport errors
    (timeout, DNS, etc.) so callers can log a breadcrumb.
    """
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"https://slack.com/api/{method}",
        data=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _restock_modal_view() -> dict:
    """Build the /bhaga-cloud restock modal: action selector + date + CSV file."""
    return {
        "type": "modal",
        "callback_id": _RESTOCK_CALLBACK_ID,
        "title": {"type": "plain_text", "text": "Restock"},
        "submit": {"type": "plain_text", "text": "Submit"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "action",
                "label": {"type": "plain_text", "text": "Action"},
                "element": {
                    "type": "static_select",
                    "action_id": "value",
                    "options": [
                        {"text": {"type": "plain_text", "text": label}, "value": label}
                        for label in _RESTOCK_ACTIONS
                    ],
                },
            },
            {
                "type": "input",
                "block_id": "delivery_date",
                "label": {"type": "plain_text", "text": "Restock delivery date"},
                "element": {"type": "datepicker", "action_id": "value"},
            },
            {
                "type": "input",
                "block_id": "csv_file",
                "optional": True,
                "label": {"type": "plain_text", "text": "Order CSV (base,quantity) — required for Add order"},
                "element": {
                    "type": "file_input",
                    "action_id": "value",
                    "filetypes": ["csv", "txt"],
                    "max_files": 1,
                },
            },
        ],
    }


def _open_restock_modal(trigger_id: str) -> Optional[str]:
    """views.open the restock modal. Returns an error string on failure, else None."""
    if not SLACK_BOT_TOKEN:
        return "BHAGA restock is unavailable — bot token not configured."
    if not trigger_id:
        return "restock: missing trigger_id."
    try:
        result = _slack_api("views.open", {"trigger_id": trigger_id, "view": _restock_modal_view()})
        if not result.get("ok"):
            log.error("views.open failed: %s", result.get("error"))
            return f"could not open restock modal ({result.get('error', 'unknown error')})."
        return None
    except Exception as exc:
        log.error("views.open failed (breadcrumb): %s", exc)
        return "could not open restock modal — see webhook logs."


def _download_slack_file(url_private_download: str) -> str:
    """Download a Slack file's content using the bot token. Returns decoded text."""
    req = urllib.request.Request(
        url_private_download,
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8-sig")


def _parse_restock_csv(text: str) -> tuple[list[tuple[str, float]], list[str]]:
    """Parse a (base, quantity) CSV. Returns (valid_rows, error_messages).

    Header row is optional — a row whose first cell doesn't parse as a known
    base AND whose second cell doesn't parse as a float is silently treated as
    a header rather than an error, so both "base,quantity\\nAçaí,12" and plain
    "Açaí,12" work.
    """
    rows: list[tuple[str, float]] = []
    errors: list[str] = []
    reader = list(csv.reader(io.StringIO(text)))
    for i, raw_row in enumerate(reader):
        if not raw_row or all(not c.strip() for c in raw_row):
            continue
        if len(raw_row) < 2:
            errors.append(f"row {i + 1}: expected 'base,quantity', got {raw_row!r}")
            continue
        base, qty_str = raw_row[0].strip(), raw_row[1].strip()
        if i == 0 and base not in _ACTIVE_BASES:
            try:
                float(qty_str)
            except ValueError:
                continue  # header row — skip
        if base not in _ACTIVE_BASES:
            errors.append(f"row {i + 1}: unknown base {base!r} (expected one of {_ACTIVE_BASES})")
            continue
        try:
            qty = float(qty_str)
        except ValueError:
            errors.append(f"row {i + 1}: quantity {qty_str!r} is not a number")
            continue
        if qty < 0:
            errors.append(f"row {i + 1}: quantity for {base} must be >= 0, got {qty}")
            continue
        rows.append((base, qty))
    # De-dup by base, last occurrence wins — a CSV with the same base twice
    # (e.g. a copy-paste mistake) should not produce two INSERT rows for the
    # same (store, delivery_date, item) grain.
    deduped = dict(rows)
    return list(deduped.items()), errors


def _restock_set_schedule(store: str, delivery_date: str, user_name: str) -> None:
    """MERGE the delivery date into inventory_restock_schedule (idempotent)."""
    fq = f"`{_BQ_RESTOCK_SCHEDULE_TABLE}`"
    _bq.query(  # type: ignore[union-attr]
        f"MERGE {fq} T"
        f" USING (SELECT @store AS store, @date AS delivery_date) S"
        f" ON T.store = S.store AND T.delivery_date = S.delivery_date"
        f" WHEN MATCHED THEN UPDATE SET updated_at = CURRENT_TIMESTAMP(), updated_by = @by"
        f" WHEN NOT MATCHED THEN INSERT (store, delivery_date, updated_at, updated_by)"
        f"   VALUES (@store, @date, CURRENT_TIMESTAMP(), @by)",
        job_config=_bq_param_config([
            ("store", "STRING", store),
            ("date", "DATE", delivery_date),
            ("by", "STRING", user_name),
        ]),
    ).result()


def _restock_clear_orders(store: str, delivery_date: str) -> None:
    """DELETE all actual-order rows for (store, delivery_date) — the 'reset to estimated' path."""
    fq = f"`{_BQ_RESTOCK_ORDERS_TABLE}`"
    _bq.query(  # type: ignore[union-attr]
        f"DELETE FROM {fq} WHERE store = @store AND delivery_date = @date",
        job_config=_bq_param_config([
            ("store", "STRING", store),
            ("date", "DATE", delivery_date),
        ]),
    ).result()


def _restock_replace_orders(
    store: str, delivery_date: str, rows: list[tuple[str, float]], user_name: str,
) -> None:
    """Replace-per-date write: DELETE then INSERT so re-uploading a corrected
    CSV for the same date always converges rather than accumulating duplicate
    rows (mirrors the idempotent-MERGE convention used elsewhere in BHAGA,
    adapted for a multi-row per-date payload where MERGE-per-row would leave
    stale items behind if the new CSV drops one).

    Not atomic (two separate BQ jobs) — a failure between DELETE and INSERT
    leaves the date with zero actuals rather than stale-but-present ones.
    Acceptable: the fallback for "no actuals" is the estimated water-fill
    (migration 031), never a crash or wrong number, and the operator can
    always re-run "Add order" to retry.
    """
    from google.cloud import bigquery as _bq_mod  # type: ignore[import]  # noqa: PLC0415

    _restock_clear_orders(store, delivery_date)
    if not rows:
        return
    fq = f"`{_BQ_RESTOCK_ORDERS_TABLE}`"
    values_sql = ", ".join(
        f"(@store, @date, @item{i}, @qty{i}, @by, CURRENT_TIMESTAMP())" for i in range(len(rows))
    )
    params = [
        _bq_mod.ScalarQueryParameter("store", "STRING", store),
        _bq_mod.ScalarQueryParameter("date", "DATE", delivery_date),
        _bq_mod.ScalarQueryParameter("by", "STRING", user_name),
    ]
    for i, (item, qty) in enumerate(rows):
        params.append(_bq_mod.ScalarQueryParameter(f"item{i}", "STRING", item))
        params.append(_bq_mod.ScalarQueryParameter(f"qty{i}", "FLOAT64", qty))
    _bq.query(  # type: ignore[union-attr]
        f"INSERT INTO {fq} (store, delivery_date, item, quantity_tubs, updated_by, updated_at)"
        f" VALUES {values_sql}",
        job_config=_bq_mod.QueryJobConfig(query_parameters=params),
    ).result()


def _handle_restock_submission(payload: dict) -> dict:
    """view_submission handler for the restock modal (Issue #137).

    Called synchronously from /slack/interactions (Slack expects a response
    within 3s for view_submission too, same deadline as slash commands — BQ
    writes here are small single-date operations so this stays fast).

    Returns a Slack view_submission response dict:
      - {"response_action": "clear"} on success (closes the modal), with a
        confirmation DM sent to the operator.
      - {"response_action": "errors", "errors": {block_id: message}} to show
        inline validation errors and keep the modal open.
    """
    store = os.environ.get("BHAGA_STORE", "palmetto")
    user_id = payload.get("user", {}).get("id", "")
    user_name = payload.get("user", {}).get("username") or user_id or "slack"
    values = payload.get("view", {}).get("state", {}).get("values", {})

    action = values.get("action", {}).get("value", {}).get("selected_option", {}).get("value")
    delivery_date = values.get("delivery_date", {}).get("value", {}).get("selected_date")
    files = values.get("csv_file", {}).get("value", {}).get("files") or []

    log.info(
        "restock view_submission: user=%s action=%r delivery_date=%s files=%d",
        user_name, action, delivery_date, len(files),
    )

    if not delivery_date:
        return {"response_action": "errors", "errors": {"delivery_date": "Delivery date is required."}}

    if action == "Add order (actuals)" and not files:
        return {"response_action": "errors", "errors": {"csv_file": "A CSV file is required for Add order."}}

    if _bq is None:
        _slack_api("chat.postMessage", {
            "channel": user_id,
            "text": ":warning: BigQuery not available — restock command is unavailable.",
        })
        return {"response_action": "clear"}

    try:
        # Always MERGE the schedule first, even before CSV validation — a
        # rejected/bad CSV submit still registers the date as "tracked"
        # (idempotent no-op if the date was already registered). This is
        # intended: the operator picked a real date regardless of whether
        # the CSV they attached was well-formed.
        _restock_set_schedule(store, delivery_date, user_name)

        if action == "Reset to estimated":
            _restock_clear_orders(store, delivery_date)
            summary = f":white_check_mark: Restock {delivery_date} reset to estimated (actuals cleared)."
        elif action == "Add order (actuals)":
            csv_text = _download_slack_file(files[0]["url_private_download"])
            rows, errors = _parse_restock_csv(csv_text)
            if errors:
                return {
                    "response_action": "errors",
                    "errors": {"csv_file": "; ".join(errors[:3]) + (" …" if len(errors) > 3 else "")},
                }
            if not rows:
                return {"response_action": "errors", "errors": {"csv_file": "CSV contained no valid (base, quantity) rows."}}
            _restock_replace_orders(store, delivery_date, rows, user_name)
            summary = (
                f":white_check_mark: Restock {delivery_date} — {len(rows)} item(s) uploaded "
                f"(by {user_name}): " + ", ".join(f"{b}={q}" for b, q in rows)
            )
        else:  # Register date only (estimated)
            summary = f":white_check_mark: Restock {delivery_date} registered (estimated — no actuals uploaded)."

        _slack_api("chat.postMessage", {"channel": user_id, "text": summary})
        return {"response_action": "clear"}
    except Exception as exc:
        log.error("restock submission failed: %s", exc)
        _slack_api("chat.postMessage", {
            "channel": user_id,
            "text": f":x: restock failed: {exc}",
        })
        return {"response_action": "clear"}


def _is_already_running(job_name: str, date_str: str) -> bool:
    """Return True if a non-terminal execution for ``date_str`` already exists.

    Queries the Cloud Run v2 executions list for the job and checks whether any
    execution that has ``REFRESH_DATE=date_str`` is still running (no completion
    time and not in a terminal state). This is the already-running guard that
    prevents the webhook from spawning a duplicate execution when the operator
    double-taps READY or Slack retries the delivery.

    Fail-open: returns False (allow the trigger) on any error so a listing
    failure never blocks a legitimately-needed resume.
    """
    try:
        from google.cloud import run_v2

        exec_client = run_v2.ExecutionsClient()
        parent = job_name  # job resource name is the parent for executions
        request = run_v2.ListExecutionsRequest(parent=parent, page_size=20)
        pager = exec_client.list_executions(request=request)

        # Terminal Cloud Run execution conditions
        _TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}

        for execution in pager:
            # Check if execution has completed
            completion_time = getattr(execution, "completion_time", None)
            if completion_time is not None:
                continue  # already finished — not a running duplicate

            # Check condition (state)
            conditions = getattr(execution, "conditions", []) or []
            for cond in conditions:
                state = getattr(cond, "state", None)
                if state is not None and str(state).split(".")[-1] in _TERMINAL:
                    # Terminal — skip
                    break
            else:
                # No terminal condition found — execution may be running.
                # Check if its REFRESH_DATE override matches.
                overrides = getattr(execution, "overrides", None)
                if overrides is None:
                    continue
                container_overrides = getattr(overrides, "container_overrides", []) or []
                for co in container_overrides:
                    env_vars = getattr(co, "env", []) or []
                    for ev in env_vars:
                        if getattr(ev, "name", "") == "REFRESH_DATE" and getattr(ev, "value", "") == date_str:
                            log.info(
                                "already-running guard: skipping trigger for date=%s "
                                "(execution %s is non-terminal)", date_str, execution.name,
                            )
                            return True
    except Exception as exc:
        log.warning("already-running check failed (fail-open): %s", exc)
    return False


# ---------------------------------------------------------------------------
# Slack-retry deduplication
# ---------------------------------------------------------------------------
#
# Slack re-delivers an event if the webhook does not respond with 200 within
# 3 seconds. Retries carry X-Slack-Retry-Num > 0 and X-Slack-Retry-Reason.
# A retried READY reply must NOT spawn a second Cloud Run execution — the
# first delivery already queued one. We dedup on two axes:
#   1. X-Slack-Retry-Num header: any value > "0" is a retry → skip processing.
#   2. event_id: record seen event IDs in Firestore for a short TTL so a retry
#      that arrives on a fresh webhook instance (after a cold start) is still
#      caught. Only READY replies (the ones that trigger jobs) are stored.
#
# The Firestore dedup collection is intentionally separate from "runs" so it
# doesn't trigger sandbox isolation checks.

_DEDUP_COLLECTION = "webhook_events"
_DEDUP_TTL_S = 300  # 5 minutes — longer than Slack's 3-retry window


def _is_slack_retry(request_headers: dict) -> bool:
    """Return True if this is a Slack delivery retry (not the first delivery)."""
    retry_num = request_headers.get("X-Slack-Retry-Num", "0")
    try:
        return int(retry_num) > 0
    except (ValueError, TypeError):
        return False


def _check_and_store_event_id(event_id: str) -> bool:
    """Store event_id in Firestore; return True if it was already seen (duplicate).

    Best-effort: returns False (not a duplicate) on any Firestore error, so a
    dedup failure never blocks a legitimate event.
    """
    if not event_id or db is None:
        return False
    try:
        ref = db.collection(_DEDUP_COLLECTION).document(event_id)
        doc = ref.get()
        if doc.exists:
            data = doc.to_dict() or {}
            # Check TTL
            seen_at = data.get("seen_at", 0)
            if time.time() - seen_at < _DEDUP_TTL_S:
                return True  # duplicate within TTL window
        ref.set({"seen_at": time.time()})
        return False
    except Exception as exc:
        log.warning("event_id dedup check failed (fail-open): %s", exc)
        return False


def _trigger_cloud_run_job_with_env(
    date_str: str,
    env_pairs: list[tuple[str, str]],
    job_name: Optional[str] = None,
) -> None:
    """Low-level: enqueue a Cloud Run Job execution with an explicit env-override list.

    ``env_pairs`` is a list of (name, value) tuples injected as container env overrides.
    ``job_name`` defaults to the CLOUD_RUN_JOB_NAME env var.

    Guards (fail-open so a guard error never blocks a legitimate resume):
    1. Already-running check: skips if a non-terminal execution for date_str already
       exists on this job (duplicate-launch guard).
    2. Slack-retry dedup applied upstream before this function is called.
    """
    job_name = job_name or os.environ.get("CLOUD_RUN_JOB_NAME")
    if not job_name:
        log.warning("CLOUD_RUN_JOB_NAME not set — cannot trigger job")
        return

    if _is_already_running(job_name, date_str):
        log.info(
            "trigger skipped — a non-terminal execution for date=%s already exists "
            "on job=%s (duplicate-launch guard)", date_str, job_name,
        )
        return

    try:
        from google.cloud import run_v2
        client = run_v2.JobsClient()
        env_overrides = [run_v2.EnvVar(name=n, value=v) for n, v in env_pairs]
        client.run_job(
            request=run_v2.RunJobRequest(
                name=job_name,
                overrides=run_v2.RunJobRequest.Overrides(
                    container_overrides=[
                        run_v2.RunJobRequest.Overrides.ContainerOverride(
                            env=env_overrides,
                        ),
                    ],
                ),
            ),
        )
        log.info("Cloud Run Job triggered for date=%s env=%s", date_str, env_pairs)
    except Exception as exc:
        log.error("Failed to trigger Cloud Run Job: %s", exc)


def _trigger_cloud_run_job(
    date_str: str,
    job_name: Optional[str] = None,
) -> None:
    """Enqueue a Cloud Run Job execution for the given date.

    Used by the READY-handshake OTP resume path (BHAGA_OTP_REQUIRE_READY=1).
    Uses the Cloud Run v2 API to create a job execution. ``job_name`` defaults to
    the prod CLOUD_RUN_JOB_NAME env var; a sandbox OTP resume passes the sandbox
    job's resource name so the reply runs the sandbox job, not prod.

    Guards (both fail-open so a guard error never blocks a legitimate resume):
    1. Already-running check: if a non-terminal execution for ``date_str`` already
       exists on this job, skip the trigger and log — prevents the webhook from
       spawning a second execution when the operator double-taps READY or Slack
       retries the delivery.
    2. The Slack-retry dedup (``_is_slack_retry`` / ``_check_and_store_event_id``)
       is applied upstream (in ``slack_events``), before this function is called.
    """
    env_pairs = [("REFRESH_DATE", date_str)]
    _trigger_cloud_run_job_with_env(date_str, env_pairs, job_name)


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

    # Slack-retry dedup: discard re-delivered events immediately.
    # Slack retries if the endpoint doesn't respond 200 within 3s; a retried
    # READY reply must NOT spawn a second Cloud Run execution.
    # Always ACK 200 so Slack stops retrying — never return a non-2xx here.
    if _is_slack_retry(dict(request.headers)):
        log.info(
            "Slack retry delivery detected (X-Slack-Retry-Num=%s) — discarding",
            request.headers.get("X-Slack-Retry-Num"),
        )
        return Response("ok", status=200)

    payload = request.get_json(force=True, silent=True)
    if payload is None:
        return Response("bad json", status=400)

    # URL verification challenge (Slack sends this during webhook setup)
    if payload.get("type") == "url_verification":
        return jsonify({"challenge": payload.get("challenge", "")})

    # Event callback
    if payload.get("type") == "event_callback":
        event_id = payload.get("event_id", "")
        # Dedup via Firestore-persisted event_id (catches duplicate deliveries
        # that slip through the retry-header check, e.g. after a cold start).
        if event_id and _check_and_store_event_id(event_id):
            log.info("Duplicate event_id=%s — discarding", event_id)
            return Response("ok", status=200)
        event = payload.get("event", {})
        _handle_event(event)

    return Response("ok", status=200)


@app.route("/slack/commands", methods=["POST"])
def slack_commands():
    # Direct sandbox trigger bypass — checked before Slack HMAC verification.
    # Only active when SANDBOX_TRIGGER_TOKEN is set (fail-closed when empty).
    # The bypass path ONLY accepts `refresh` commands — all other commands
    # (config/training/alias/exclude) write to prod BQ and must always go
    # through the Slack HMAC path. Non-refresh via this bypass → 403.
    sandbox_token = request.headers.get("X-Sandbox-Trigger", "")
    if _SANDBOX_TRIGGER_TOKEN and hmac.compare_digest(sandbox_token, _SANDBOX_TRIGGER_TOKEN):
        cmd_text = (request.form.get("text") or "").strip().lower()
        if not cmd_text.startswith("refresh"):
            return Response(
                "sandbox trigger only supports refresh commands", status=403
            )
        return _handle_slash_command(request.form, sandbox=True)
    # A non-empty header that doesn't match → 403 (not a Slack call and not
    # an authorized sandbox trigger). An empty/missing header falls through
    # to normal Slack HMAC verification below.
    if sandbox_token:
        return Response("invalid sandbox trigger token", status=403)

    body = request.get_data()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not verify_slack_signature(body, timestamp, signature):
        return Response("invalid signature", status=403)

    return _handle_slash_command(request.form)


@app.route("/slack/interactions", methods=["POST"])
def slack_interactions():
    """Slack interactivity endpoint — handles the restock modal's view_submission.

    Slack posts interactivity payloads as a form field named "payload"
    containing a JSON string (not a raw JSON body), so this route parses the
    body differently from /slack/events and /slack/commands.
    """
    body = request.get_data()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not verify_slack_signature(body, timestamp, signature):
        return Response("invalid signature", status=403)

    try:
        payload = json.loads(request.form.get("payload", "{}"))
    except json.JSONDecodeError:
        return Response("bad payload", status=400)

    if payload.get("type") == "view_submission" and payload.get("view", {}).get("callback_id") == _RESTOCK_CALLBACK_ID:
        result = _handle_restock_submission(payload)
        return jsonify(result)

    return Response("", status=200)


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
