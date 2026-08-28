"""Turn a stream of per-poll sightings into at most one email per visit.

Kept pure (no Firestore, no clock) so the notification rules are unit-testable:
`decide` takes the stored state plus this poll's outcome and returns the next
state. Everything the state machine needs is in its arguments.

Rules, in order of precedence:
  1. A sighting while an episode is already open never re-notifies.
  2. A sighting after the pup has been unseen for `absence_minutes` starts a new
     episode and notifies.
  3. No email may follow another inside `notify_cooldown_minutes`, whatever the
     episode bookkeeping says. This is the backstop against a flapping detector
     emailing repeatedly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .config import Settings


@dataclass(frozen=True)
class Decision:
    state: dict[str, Any]
    notify: bool
    reason: str
    episode_started: bool = False
    episode_ended: bool = False


def _f(state: dict, key: str) -> Optional[float]:
    value = state.get(key)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def decide(
    state: dict[str, Any] | None,
    *,
    seen: bool,
    now: float,
    settings: Settings,
    camera: str = "",
) -> Decision:
    """Fold one poll outcome into the episode state."""
    state = dict(state or {})
    absence_s = settings.absence_minutes * 60.0
    cooldown_s = settings.notify_cooldown_minutes * 60.0

    episode_active = bool(state.get("episode_active"))
    last_seen = _f(state, "last_seen_ts")
    last_notified = _f(state, "last_notified_ts")

    state["last_poll_ts"] = now

    if not seen:
        # Close the episode once he has been gone long enough that the next
        # sighting is genuinely a new visit rather than a gap in detection.
        if episode_active and last_seen is not None and (now - last_seen) >= absence_s:
            state["episode_active"] = False
            state["episode_ended_ts"] = now
            return Decision(state, False, "episode_closed", episode_ended=True)
        return Decision(state, False, "not_seen")

    state["last_seen_ts"] = now
    state["last_camera"] = camera or state.get("last_camera", "")

    if episode_active:
        return Decision(state, False, "already_in_episode")

    # A sighting inside the previous episode's absence window is treated as the
    # same visit (he stepped behind the playhouse), not a new one.
    if last_seen is not None and (now - last_seen) < absence_s and state.get("episode_ended_ts") is None:
        return Decision(state, False, "within_absence_window")

    if last_notified is not None and (now - last_notified) < cooldown_s:
        state["episode_active"] = True
        state["episode_started_ts"] = now
        return Decision(state, False, "cooldown_active", episode_started=True)

    state["episode_active"] = True
    state["episode_started_ts"] = now
    state["episode_ended_ts"] = None
    state["last_notified_ts"] = now
    return Decision(state, True, "episode_started", episode_started=True)
