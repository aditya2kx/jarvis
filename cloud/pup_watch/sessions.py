"""Start and stop a monitoring session.

Extracted so the HTTP endpoints and the email-reply control path cannot drift:
"start" must mean exactly the same thing whether it arrives as an authenticated
POST or as a reply to a sighting email.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional, Sequence

from . import persist
from .config import Settings

log = logging.getLogger("pup_watch")


def start(
    *,
    hours: Optional[float] = None,
    by: str = "operator",
    cameras: Optional[Sequence[str]] = None,
    settings: Settings,
    now: Optional[float] = None,
) -> dict[str, Any]:
    now = time.time() if now is None else now
    # No duration means "keep watching until I say stop" — the operator's
    # default, since he does not know in advance when the pup comes home. A
    # duration is still honoured when given, and is still capped.
    if hours is None:
        wanted = None
    else:
        try:
            wanted = float(hours)
        except (TypeError, ValueError):
            # Asking for a duration and getting it wrong must not be rewarded
            # with an unbounded session — fall back to the bounded ceiling.
            wanted = settings.session_max_hours
    if wanted is not None:
        wanted = max(0.25, min(wanted, settings.session_max_hours))
    session = {
        "active": True,
        "started_ts": now,
        "started_by": str(by),
        "open_ended": wanted is None,
        "stop_after_ts": None if wanted is None else now + wanted * 3600,
        "cameras": [str(c) for c in cameras] if cameras else None,
        "stopped_ts": None,
    }
    persist.save_session(session)
    # Clear episode bookkeeping so a fresh session can notify immediately
    # instead of inheriting the previous outing's cooldown.
    persist.save_state({
        "episode_active": False,
        "episode_started_ts": None,
        "episode_ended_ts": None,
        "last_seen_ts": None,
        "last_notified_ts": None,
    })
    log.info("pup-watch session_started hours=%s by=%s cameras=%s",
             "open-ended" if wanted is None else f"{wanted:.2f}", by, session["cameras"])
    return {"session": session, "hours": wanted}


def stop(*, by: str = "operator", now: Optional[float] = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    persist.save_session({"active": False, "stopped_ts": now, "stopped_by": str(by)})
    log.info("pup-watch session_stopped by=%s", by)
    return {"active": False}
