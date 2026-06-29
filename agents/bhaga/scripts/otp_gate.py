#!/usr/bin/env python3
"""OTP availability gate for the BHAGA daily refresh.

Default behaviour (BHAGA_OTP_REQUIRE_READY unset / "0"):
  The nightly run proceeds inline to the portal scrape. If ADP actually
  challenges for a 2FA code, the runner posts a Slack OTP-code ask and blocks
  up to BHAGA_OTP_WAIT_S (default 900 s). On no-reply timeout the ADP step
  raises OtpWaitTimeout, which daily_refresh treats as a graceful skip (alert
  posted, run finishes on existing data, next nightly retries). No READY
  handshake is needed on a trusted-device night — ADP's persisted session
  clears 2FA silently.

Opt-in rollback (BHAGA_OTP_REQUIRE_READY=1):
  Restores the legacy two-step READY handshake + checkpoint-and-resume model:
  1. On a run that WILL trigger an OTP, post a READY request, persist a
     pending checkpoint, and exit cleanly (exit 0). OTP codes expire in
     minutes; we never pre-send and block for hours.
  2. ONE READY (operator reply in the BHAGA Slack DM) covers ALL OTP portals.
     On resume, drive each portal back-to-back in a short bounded window.
  3. No reply within 48 h → skip the OTP-gated steps, alert, next nightly
     retries. Zero idle compute cost.

BHAGA_OTP_ASSUME_READY=1 (sandbox/supervised runs): skip the gate entirely
and drive OTP portals inline regardless of require-ready mode.

The checkpoint is stored via skills.bhaga_config.state_adapter (Firestore in
cloud, local disk in tests) so both environments share the same code.
"""

from __future__ import annotations

import datetime
import os
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")

# How long (from when the READY request was posted) we wait for a READY reply
# before giving up and skipping ONLY the OTP-gated step(s). Both non-cloud and
# cloud use the same cap.
DEFAULT_CAP_HOURS = 48

# Lenient, case-insensitive set of words that count as "I'm available now".
# Deliberately small + robust — we match the whole reply OR its leading token
# (so "ready to go" / "ok grabbing phone" both count) but never digits (an OTP
# code must NOT be mistaken for a READY).
READY_WORDS = {
    "ready",
    "ok",
    "okay",
    "go",
    "yes",
    "yep",
    "yup",
    "available",
    "here",
    "y",
}

# Gate decisions.
PROCEED = "proceed"            # READY already received → drive the OTP portals
EXIT_PENDING = "exit_pending"  # no READY yet → checkpoint + exit cleanly
SKIP_OTP = "skip_otp"          # 48h elapsed with no READY → skip OTP steps


class PendingOtpAvailability(Exception):
    """Control-flow signal raised when a run hits an OTP gate without READY.

    Carries the portals that still need an OTP so the orchestrator can
    checkpoint + post a single READY request, then exit 0 (clean) rather
    than crash. Catch this at the orchestrator boundary.
    """

    def __init__(self, portals):
        self.portals = list(portals)
        super().__init__(
            "pending OTP availability for portals: " + ", ".join(self.portals)
        )


class OtpWaitTimeout(Exception):
    """Raised by the ADP runner when the operator does not reply with an OTP
    code within the inline wait window (BHAGA_OTP_WAIT_S seconds).

    Distinct from a selector/scrape failure so daily_refresh can treat it as
    a graceful skip (post an alert, continue on existing ADP data, do not
    trip the pipeline halt breaker) rather than a hard failure.
    """


class AdpLoginThrottled(Exception):
    """Raised when ADP serves its sorry.adp.com interstitial during login.

    Two surfaces raise this:
      - the login-form throttle (``_wait_for_login_form`` exhausts its retries), and
      - a post-login redirect to sorry.adp.com (``_ensure_logged_in``), e.g. during
        a scheduled RUN maintenance window.

    daily_refresh treats it as a graceful ADP skip — alert, continue on existing
    ADP data, do not trip the pipeline halt breaker — identical to OtpWaitTimeout.

    ``retry_at`` (UTC-aware datetime, optional) carries the maintenance-window end
    + buffer when it could be parsed from the page banner. When present,
    daily_refresh schedules a one-shot smart retry at that time instead of waiting
    for the next nightly. When None, the next nightly / Retry-Dates re-attempts.
    """

    def __init__(self, *args, retry_at=None):
        super().__init__(*args)
        self.retry_at = retry_at


def is_ready_reply(text) -> bool:
    """Return True if a Slack reply means "I'm available — send the codes".

    Case-insensitive, tolerant of trailing punctuation, and matches either
    the whole message or its first token. Never matches a numeric OTP code.
    """
    if not text:
        return False
    cleaned = str(text).strip().lower()
    # Strip surrounding punctuation/emphasis the operator might add.
    cleaned = cleaned.strip("!.?*_`~ ")
    if not cleaned:
        return False
    if cleaned in READY_WORDS:
        return True
    tokens = cleaned.split()
    return bool(tokens) and tokens[0] in READY_WORDS


def _parse_ts(raw) -> datetime.datetime | None:
    if not raw:
        return None
    try:
        return datetime.datetime.fromisoformat(str(raw))
    except (ValueError, TypeError):
        return None


def evaluate(
    refresh_date: datetime.date,
    otp_portals: list[str],
    *,
    now: datetime.datetime | None = None,
    cap_hours: int = DEFAULT_CAP_HOURS,
    get_pending=None,
    force_request: bool | None = None,
) -> tuple[str, dict]:
    """Decide what the orchestrator should do at the OTP availability gate.

    State access is injected via ``get_pending`` (defaults to
    ``state_adapter.get_pending_otp``) so this stays unit-testable without a
    real backend.

    Returns ``(decision, info)`` where decision is one of PROCEED /
    EXIT_PENDING / SKIP_OTP and ``info`` carries diagnostic context plus, for
    EXIT_PENDING, a ``first_request`` flag telling the caller whether it needs
    to post a NEW READY request (True) or a request is already outstanding
    (False — just re-exit without re-pinging the operator).

    Default mode (BHAGA_OTP_REQUIRE_READY unset):
      Always returns PROCEED so the nightly scrape starts immediately. If ADP
      actually challenges for a 2FA code, the runner posts a Slack OTP-code ask
      inline and raises OtpWaitTimeout on no reply, which the caller treats as a
      graceful skip.

    Rollback mode (BHAGA_OTP_REQUIRE_READY=1):
      Restores the legacy READY handshake: no READY → EXIT_PENDING (checkpoint
      + exit 0); READY received → PROCEED; 48 h elapsed → SKIP_OTP.

    ``force_request`` (relevant only in rollback mode; defaults to env
    ``BHAGA_OTP_FORCE_REQUEST == "1"``) makes an explicit operator-driven trigger
    re-post a fresh READY request when a stale checkpoint exists.
    """
    if not otp_portals:
        # Zero-OTP happy path — nothing will launch a browser this run.
        return PROCEED, {"reason": "no OTP portals needed"}

    if os.environ.get("BHAGA_OTP_ASSUME_READY") == "1":
        # Operator-supervised run (e.g. a live sandbox run): drive OTP portals
        # inline regardless of require-ready mode.
        return PROCEED, {
            "reason": "assume-ready (operator supervising — inline OTP)",
            "portals": list(otp_portals),
        }

    if os.environ.get("BHAGA_OTP_REQUIRE_READY") != "1":
        # Default inline-autostart mode: proceed immediately; runner handles
        # the real OTP ask (if any) and raises OtpWaitTimeout on no reply.
        return PROCEED, {
            "reason": "inline OTP autostart (no READY handshake required)",
            "portals": list(otp_portals),
        }

    # ── Legacy READY handshake (BHAGA_OTP_REQUIRE_READY=1) ──────────────────
    if now is None:
        now = datetime.datetime.now(CT)
    if force_request is None:
        force_request = os.environ.get("BHAGA_OTP_FORCE_REQUEST") == "1"
    if get_pending is None:
        from skills.bhaga_config.state_adapter import get_pending_otp as get_pending

    pending = get_pending(refresh_date)

    if pending is None:
        return EXIT_PENDING, {
            "reason": "no checkpoint — posting first READY request",
            "first_request": True,
            "portals": list(otp_portals),
        }

    if pending.get("ready_received"):
        return PROCEED, {
            "reason": "READY received — driving OTP portals",
            "portals": pending.get("portals") or list(otp_portals),
            "ready_at": pending.get("ready_at"),
        }

    if force_request:
        # Explicit operator-driven trigger (manual /bhaga-cloud refresh or
        # Retry-Dates deploy rerun): re-post a fresh READY request to reset
        # the 48 h window instead of silently deferring.
        return EXIT_PENDING, {
            "reason": "force re-request (explicit trigger) — re-posting READY",
            "first_request": True,
            "portals": pending.get("portals") or list(otp_portals),
        }

    requested_at = _parse_ts(pending.get("requested_at"))
    if requested_at is not None:
        # Normalize naive timestamps to CT so the subtraction never raises.
        if requested_at.tzinfo is None:
            requested_at = requested_at.replace(tzinfo=CT)
        age_hours = (now - requested_at).total_seconds() / 3600.0
        if age_hours >= cap_hours:
            return SKIP_OTP, {
                "reason": f"no READY within {cap_hours}h — skipping OTP steps",
                "age_hours": age_hours,
                "portals": pending.get("portals") or list(otp_portals),
            }

    return EXIT_PENDING, {
        "reason": "awaiting READY (request already outstanding)",
        "first_request": False,
        "portals": pending.get("portals") or list(otp_portals),
    }


def portals_label(portals: list[str]) -> str:
    """Human-readable join of portal names for Slack messages."""
    portals = list(portals)
    if not portals:
        return ""
    if len(portals) == 1:
        return portals[0]
    if len(portals) == 2:
        return f"{portals[0]} and {portals[1]}"
    return ", ".join(portals[:-1]) + f", and {portals[-1]}"
