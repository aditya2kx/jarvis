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
    try:
        wanted = float(hours) if hours is not None else settings.session_max_hours
    except (TypeError, ValueError):
        wanted = settings.session_max_hours
    wanted = max(0.25, min(wanted, settings.session_max_hours))
    session = {
        "active": True,
        "started_ts": now,
        "started_by": str(by),
        "stop_after_ts": now + wanted * 3600,
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
    log.info("pup-watch session_started hours=%.2f by=%s cameras=%s", wanted, by, session["cameras"])
    return {"session": session, "hours": wanted}


def stop(*, by: str = "operator", now: Optional[float] = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    persist.save_session({"active": False, "stopped_ts": now, "stopped_by": str(by)})
    log.info("pup-watch session_stopped by=%s", by)
    return {"active": False}
