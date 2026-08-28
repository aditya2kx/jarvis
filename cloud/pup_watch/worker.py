"""One poll of the daycare yard: frames in, maybe an email out.

Ordered cheapest-stage-first so the expensive stages almost never run:

    session check  ->  ~1ms, and skips everything when monitoring is off
    frame grab     ->  ~1s
    local detector ->  ~270ms per pass, only on grabbed frames
    Gemini re-ID   ->  only when >=min_hits frames already agree
    email          ->  only on a new episode

The dog count is a hard veto, not a score: the pup is only ever let out alone,
so two dogs in the yard means it is not him no matter how cream one of them is.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from . import episode, identify, notify, persist, stream, vision
from .config import Camera, Settings, load_cameras, settings_with_overlay

log = logging.getLogger("pup_watch")


@dataclass
class CameraResult:
    camera: str
    label: str = ""
    frames: int = 0
    hits: int = 0
    seen: bool = False
    reason: str = ""
    dogs: int = 0
    persons: int = 0
    dog_box_px: int = 0
    cream_fraction: Optional[float] = None
    identity: Optional[identify.Identification] = None
    best_frame: Optional[bytes] = field(default=None, repr=False)
    best_detections: tuple[vision.Detection, ...] = field(default=(), repr=False)

    def to_log(self) -> dict[str, Any]:
        return {
            "camera": self.camera,
            "frames": self.frames,
            "hits": self.hits,
            "seen": self.seen,
            "reason": self.reason,
            "dogs": self.dogs,
            "persons": self.persons,
        }


def session_active(session: dict, *, now: float, settings: Settings) -> tuple[bool, str]:
    """Is monitoring on? Auto-expires so a forgotten session stops polling."""
    if not session or not session.get("active"):
        return False, "no_active_session"
    started = session.get("started_ts")
    try:
        started_f = float(started) if started is not None else None
    except (TypeError, ValueError):
        started_f = None
    stop_after = session.get("stop_after_ts")
    try:
        stop_after_f = float(stop_after) if stop_after is not None else None
    except (TypeError, ValueError):
        stop_after_f = None
    if stop_after_f is not None and now >= stop_after_f:
        return False, "session_expired_stop_after"
    if started_f is not None and (now - started_f) > settings.session_max_hours * 3600:
        return False, "session_expired_max_hours"
    return True, "active"


def evaluate_camera(camera: Camera, settings: Settings) -> CameraResult:
    """Grab and analyse this camera's frames. Never raises."""
    result = CameraResult(camera=camera.name, label=camera.label)
    try:
        info = stream.resolve_stream(camera.alias)
    except stream.StreamUnavailable as e:
        result.reason = f"stream_unavailable {e}"
        log.error("pup-watch fail reason=stream_resolve camera=%s err=%s", camera.name, e)
        return result
    if not info.usable:
        result.reason = "camera_offline"
        log.info("pup-watch skip reason=camera_offline camera=%s health=%s", camera.name, info.health)
        return result

    try:
        frames = stream.grab_frames(
            info.playlist_url,
            count=settings.frames_per_poll,
            interval_s=settings.frame_interval_s,
        )
    except stream.StreamUnavailable as e:
        result.reason = f"frame_grab_failed {e}"
        log.error("pup-watch fail reason=frame_grab camera=%s err=%s", camera.name, e)
        return result

    result.frames = len(frames)
    best_score = -1.0
    for raw in frames:
        try:
            verdict = vision.analyse_frame(raw, settings=settings, regions=camera.yard_regions)
        except Exception as e:  # noqa: BLE001 — one bad frame must not kill the poll
            log.error("pup-watch fail reason=analyse_frame camera=%s err=%r", camera.name, e)
            continue
        result.dogs = max(result.dogs, len(verdict.dogs))
        result.persons = max(result.persons, len(verdict.persons))
        if not verdict.lone_cream_dog:
            result.reason = result.reason or verdict.reason
            continue
        result.hits += 1
        best = verdict.best_dog
        if best is not None and best.score > best_score:
            best_score = best.score
            result.best_frame = raw
            result.best_detections = tuple(verdict.dogs) + tuple(verdict.persons)
            result.dog_box_px = int(best.height)
            result.cream_fraction = verdict.cream.fraction if verdict.cream else None

    if result.hits < settings.min_hits_per_poll:
        # Keep both facts: how close we got, and why the frames failed.
        detail = result.reason or "no_candidate"
        result.reason = f"insufficient_hits {result.hits}/{settings.min_hits_per_poll} last={detail}"
        return result

    result.reason = "lone_cream_dog"
    result.seen = True

    if settings.require_gemini_confirm and result.best_frame is not None and result.best_detections:
        dog = max((d for d in result.best_detections if d.cls == 16), key=lambda d: d.score, default=None)
        if dog is not None:
            crop = vision.crop_detection(result.best_frame, dog.box)
            ident = identify.confirm_pup(
                crop, model=settings.gemini_model, confidence_min=settings.gemini_confidence_min
            )
            result.identity = ident
            if ident.conclusive and not ident.is_pup:
                # A confident "different dog" overrides the local stages; an
                # inconclusive check (no key, no refs, API error) must not
                # silently suppress a real sighting.
                result.seen = False
                result.reason = f"identity_rejected confidence={ident.confidence:.2f}"
    return result


def tick(*, now: Optional[float] = None) -> dict[str, Any]:
    """One scheduled poll across every configured camera."""
    now = time.time() if now is None else now
    settings = settings_with_overlay(persist.load_config())
    session = persist.load_session()

    active, why = session_active(session, now=now, settings=settings)
    if not active:
        if session.get("active") and why.startswith("session_expired"):
            persist.save_session({"active": False, "stopped_ts": now, "stopped_by": why})
            log.info("pup-watch session_auto_stopped reason=%s", why)
        return {"polled": False, "reason": why}

    cameras = load_cameras()
    only = session.get("cameras")
    if isinstance(only, list) and only:
        wanted = {str(c) for c in only}
        cameras = [c for c in cameras if c.name in wanted] or cameras

    results: list[CameraResult] = []
    notified = False
    state = persist.load_state()

    for camera in cameras:
        result = evaluate_camera(camera, settings)
        results.append(result)
        decision = episode.decide(
            state, seen=result.seen, now=now, settings=settings, camera=camera.name
        )
        state = decision.state
        log.info(
            "pup-watch poll camera=%s seen=%s hits=%d/%d dogs=%d persons=%d decision=%s reason=%s",
            result.camera, result.seen, result.hits, result.frames,
            result.dogs, result.persons, decision.reason, result.reason,
        )
        if decision.notify:
            image = None
            if result.best_frame is not None:
                try:
                    image = vision.annotate(result.best_frame, result.best_detections)
                except Exception as e:  # noqa: BLE001
                    log.error("pup-watch fail reason=annotate err=%r", e)
            fields: dict[str, Any] = {
                "camera": result.camera,
                "camera_label": result.label,
                "seen_ts": now,
                "dogs": result.dogs,
                "persons": result.persons,
                "hits": result.hits,
                "frames": result.frames,
                "dog_box_px": result.dog_box_px or None,
                "cream_fraction": result.cream_fraction,
            }
            if result.identity is not None:
                if result.identity.conclusive:
                    fields["identity_confidence"] = result.identity.confidence
                    fields["identity_notes"] = result.identity.notes
                else:
                    fields["identity_skipped"] = result.identity.skipped
            notified = notify.send_sighting(fields, image) or notified

    persist.save_state(state)
    return {
        "polled": True,
        "notified": notified,
        "cameras": [r.to_log() for r in results],
        "state": {k: state.get(k) for k in ("episode_active", "last_seen_ts", "last_notified_ts")},
    }
