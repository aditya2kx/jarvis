"""Tunables + camera source of truth for pup-watch.

Every threshold here was picked from a measured sweep on real yard frames
(see cloud/pup_watch/README.md § Why these thresholds), not guessed. Firestore
can overlay the numeric knobs at runtime so tuning does not need a redeploy.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

CAMERAS_FILE = Path(__file__).with_name("cameras.json")

# COCO class ids emitted by the detector we ship.
COCO_DOG = 16
COCO_PERSON = 0


@dataclass(frozen=True)
class Camera:
    name: str
    alias: str
    label: str = ""
    yard_regions: tuple[tuple[int, int, int, int], ...] = ()


@dataclass(frozen=True)
class Settings:
    # --- frame sampling ---
    frames_per_poll: int = 4
    frame_interval_s: float = 2.0

    # --- detection ---
    # 0.25 sits below every true-positive score measured at >=55px while staying
    # far above the 0.018 ceiling the empty yard produced.
    dog_score_min: float = 0.25
    person_score_min: float = 0.30
    # Below ~50px the detector is unreliable; boxes smaller than this are not
    # trusted as the pup even if scored.
    dog_min_box_px: int = 40

    # --- cream-coat gate ---
    # An English Cream Golden on turf reads as bright and desaturated.
    cream_brightness_min: float = 150.0
    cream_saturation_max: float = 85.0
    cream_pixel_fraction_min: float = 0.30

    # --- decision ---
    # Frames in one poll that must independently look like "one cream dog".
    min_hits_per_poll: int = 2
    # A second dog anywhere in the frame means it is not him (he is only ever
    # let out alone), so this is a hard veto rather than a score penalty.
    max_dogs: int = 1

    # --- episode / notification ---
    # Re-notify only after he has been gone this long, so one visit is one email.
    absence_minutes: int = 20
    # Hard floor between emails regardless of episode churn.
    notify_cooldown_minutes: int = 10

    # --- session ---
    # A forgotten session stops polling instead of running forever.
    session_max_hours: float = 12.0

    # --- identity confirmation ---
    require_gemini_confirm: bool = True
    gemini_model: str = "gemini-2.5-flash-lite"
    gemini_confidence_min: float = 0.55


_NUMERIC_OVERLAY_KEYS = {
    "frames_per_poll",
    "frame_interval_s",
    "dog_score_min",
    "person_score_min",
    "dog_min_box_px",
    "cream_brightness_min",
    "cream_saturation_max",
    "cream_pixel_fraction_min",
    "min_hits_per_poll",
    "max_dogs",
    "absence_minutes",
    "notify_cooldown_minutes",
    "session_max_hours",
    "gemini_confidence_min",
}


def load_cameras(path: Path | None = None) -> list[Camera]:
    raw = json.loads((path or CAMERAS_FILE).read_text())
    out: list[Camera] = []
    for entry in raw.get("cameras", []):
        regions = tuple(tuple(int(v) for v in r) for r in entry.get("yard_regions", ()))
        out.append(
            Camera(
                name=str(entry["name"]),
                alias=str(entry["alias"]),
                label=str(entry.get("label", "")),
                yard_regions=regions,  # type: ignore[arg-type]
            )
        )
    if not out:
        raise ValueError("cameras.json defines no cameras")
    return out


def settings_with_overlay(overlay: dict[str, Any] | None) -> Settings:
    """Apply a Firestore config overlay to the defaults, ignoring junk keys."""
    base = Settings()
    if not overlay:
        return base
    patch: dict[str, Any] = {}
    for key in _NUMERIC_OVERLAY_KEYS:
        if key not in overlay or overlay[key] is None:
            continue
        current = getattr(base, key)
        try:
            patch[key] = type(current)(overlay[key])
        except (TypeError, ValueError):
            continue
    if isinstance(overlay.get("require_gemini_confirm"), bool):
        patch["require_gemini_confirm"] = overlay["require_gemini_confirm"]
    if isinstance(overlay.get("gemini_model"), str) and overlay["gemini_model"].strip():
        patch["gemini_model"] = overlay["gemini_model"].strip()
    return replace(base, **patch)


def notify_recipients() -> list[str]:
    """Recipients come from env, never from git — these are personal addresses."""
    raw = os.environ.get("PUPWATCH_NOTIFY_TO", "")
    return [a.strip() for a in raw.replace(";", ",").split(",") if a.strip()]
