"""Confirm a candidate dog is *our* pup, using reference photos + Gemini.

Counting dogs and checking for a cream coat gets us most of the way, but it
cannot tell our English Cream Golden from someone else's cream dog let out
alone. This stage closes that gap: it only runs on frames the cheap local
stages already flagged, so a handful of calls a day covers a full month for
cents.

Reference photos live in GCS (or a local dir in dev), never in git — they are
personal photos and they should be addable without a redeploy.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

log = logging.getLogger("pup_watch")

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_PROMPT = """You are verifying a sighting from a dog-daycare yard camera.

The first images are reference photos of ONE specific dog: a white English
Cream Golden Retriever, 80 lb, 2 years old.

The LAST image is a crop from the yard camera. It is low resolution and may be
motion-blurred; judge coat colour, body shape, ear shape, tail and build.

Answer strictly about whether the dog in the last image is the SAME dog as in
the reference photos. If the last image contains no dog, or more than one dog,
say so. Do not guess to be helpful: if the crop is too small or blurry to tell,
return a low confidence.
"""

_SCHEMA = {
    "type": "object",
    "properties": {
        "is_pup": {"type": "boolean"},
        "confidence": {"type": "number"},
        "dogs_visible": {"type": "integer"},
        "coat": {"type": "string"},
        "notes": {"type": "string"},
    },
    "required": ["is_pup", "confidence", "dogs_visible"],
}


@dataclass(frozen=True)
class Identification:
    is_pup: bool
    confidence: float
    dogs_visible: int
    coat: str = ""
    notes: str = ""
    skipped: str = ""

    @property
    def conclusive(self) -> bool:
        return not self.skipped


def _api_key() -> str:
    return (os.environ.get("PUPWATCH_GEMINI_KEY") or os.environ.get("GEMINI_API_KEY") or "").strip()


def _read_uri(uri: str) -> Optional[bytes]:
    uri = uri.strip()
    if not uri:
        return None
    if uri.startswith("gs://"):
        try:
            from google.cloud import storage

            bucket, _, blob = uri[len("gs://"):].partition("/")
            client = storage.Client()
            return client.bucket(bucket).blob(blob).download_as_bytes()
        except Exception as e:  # noqa: BLE001 — a missing reference is not fatal
            log.error("pup-watch fail reason=reference_fetch uri=%s err=%r", uri, e)
            return None
    path = Path(uri)
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError as e:
        log.error("pup-watch fail reason=reference_read uri=%s err=%r", uri, e)
        return None


@lru_cache(maxsize=1)
def _reference_images_cached(spec: str) -> tuple[bytes, ...]:
    out = [b for b in (_read_uri(u) for u in spec.split(",")) if b]
    log.info("pup-watch references_loaded n=%d", len(out))
    return tuple(out)


def reference_images() -> tuple[bytes, ...]:
    spec = os.environ.get("PUPWATCH_REFERENCE_URIS", "").strip()
    return _reference_images_cached(spec) if spec else ()


def _post(url: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"gemini_status={e.code} body={body}") from e


def confirm_pup(
    crop_jpeg: bytes,
    *,
    model: str,
    confidence_min: float,
    timeout: float = 30.0,
) -> Identification:
    """Ask Gemini whether `crop_jpeg` shows our pup. Never raises."""
    key = _api_key()
    refs = reference_images()
    if not key:
        return Identification(False, 0.0, 0, skipped="no_api_key")
    if not refs:
        return Identification(False, 0.0, 0, skipped="no_reference_images")

    parts: list[dict] = [{"text": _PROMPT}]
    for ref in refs:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(ref).decode()}})
    parts.append({"text": "Camera crop to verify:"})
    parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(crop_jpeg).decode()}})

    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0.0,
            "responseMimeType": "application/json",
            "responseSchema": _SCHEMA,
        },
    }
    url = f"{API_URL.format(model=model)}?{urllib.parse.urlencode({'key': key})}"
    try:
        data = _post(url, payload, timeout)
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
    except Exception as e:  # noqa: BLE001 — identity check is best-effort
        log.error("pup-watch fail reason=gemini_confirm err=%r", e)
        return Identification(False, 0.0, 0, skipped=f"error:{type(e).__name__}")

    confidence = float(parsed.get("confidence") or 0.0)
    dogs = int(parsed.get("dogs_visible") or 0)
    is_pup = bool(parsed.get("is_pup")) and confidence >= confidence_min and dogs == 1
    ident = Identification(
        is_pup=is_pup,
        confidence=confidence,
        dogs_visible=dogs,
        coat=str(parsed.get("coat") or ""),
        notes=str(parsed.get("notes") or ""),
    )
    log.info(
        "pup-watch identity is_pup=%s confidence=%.2f dogs=%d coat=%s",
        ident.is_pup, ident.confidence, ident.dogs_visible, ident.coat,
    )
    return ident
