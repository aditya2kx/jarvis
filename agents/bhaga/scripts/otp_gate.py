#!/usr/bin/env python3
"""Two-step OTP availability gate for the BHAGA daily refresh.

Implements the "READY handshake + checkpoint-and-resume" model so the
operator can be unavailable for a long time without a run failing or
burning compute waiting on an OTP that would expire anyway.

The flow (generalized to ALL OTP-needing portals, not just Square):

  1. When a run reaches a step that WILL trigger an OTP, it does NOT send
     the OTP immediately. Instead it asks the operator if they're available
     ("reply READY when you can grab your phone"), persists a pending
     checkpoint, and EXITS CLEANLY. OTP codes expire in minutes, so we never
     pre-send a code and block for hours.

  2. ONE READY covers ALL OTP portals the run will need. When the operator
     replies READY (anytime within the 48h cap), the run resumes from the
     checkpoint and drives each OTP portal back-to-back, triggering a FRESH
     OTP per portal and taking the reply in a short bounded window while the
     operator is actively engaged.

  3. If no READY arrives within 48h of the request, the OTP-gated step(s) are
     skipped, everything else finishes, an alert is posted, and the next
     nightly run retries. The long wait costs nothing (the laptop is closed /
     the Cloud Run job has exited).

This module is the backend-agnostic brain. The checkpoint itself is stored
via skills.bhaga_config.state_adapter (Firestore in cloud, disk locally) so
the same code resumes in either environment.
"""

from __future__ import annotations

import datetime
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
    """
    if not otp_portals:
        # Zero-OTP happy path — nothing will launch a browser this run.
        return PROCEED, {"reason": "no OTP portals needed"}

    if now is None:
        now = datetime.datetime.now(CT)
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
