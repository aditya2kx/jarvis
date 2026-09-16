"""Pull frames from the daycare's public ipcamlive HLS stream.

The stream id rotates, so the playlist URL is resolved from the camera alias on
every poll rather than pinned in config. The page itself needs no auth: the
alias is enough to get the stream-state JSON, which carries the media host and
current stream id.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("pup_watch")

STATE_URL = "https://x.ipcamlive.com/ajax/getcamerastreamstate.php"
PAGE_URL = "https://x.ipcamlive.com/{alias}"
_UA = "Mozilla/5.0 (compatible; jarvis-pup-watch/1.0)"


class StreamUnavailable(RuntimeError):
    """Camera is offline or the playlist could not be resolved."""


@dataclass(frozen=True)
class StreamInfo:
    alias: str
    playlist_url: str
    available: bool
    health: Optional[int]
    motion_diff: Optional[float]
    brightness: Optional[float]

    @property
    def usable(self) -> bool:
        return self.available and bool(self.playlist_url)


def _get(url: str, *, referer: str, timeout: float = 15.0) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Referer": referer}, method="GET"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _f(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_stream(alias: str, *, timeout: float = 15.0) -> StreamInfo:
    """Ask ipcamlive where this camera's live playlist currently lives."""
    referer = PAGE_URL.format(alias=alias)
    url = f"{STATE_URL}?{urllib.parse.urlencode({'cameraalias': alias})}"
    try:
        payload = json.loads(_get(url, referer=referer, timeout=timeout).decode())
    except Exception as e:  # noqa: BLE001 — any failure is "camera unavailable"
        raise StreamUnavailable(f"state_fetch_failed alias={alias} err={e!r}") from e

    details = payload.get("details") or {}
    info = payload.get("streaminfo") or {}
    quality = info.get("quality") or {}

    available = str(details.get("streamavailable", "0")) == "1"
    address = str(details.get("address") or "").rstrip("/")
    stream_id = str(details.get("streamid") or "")

    playlist = ""
    if address and stream_id:
        # ipcamlive advertises http:// but serves https, and we would rather not
        # pull video over plaintext.
        if address.startswith("http://"):
            address = "https://" + address[len("http://") :]
        levels = ((info.get("live") or {}).get("levels") or [{}])
        leaf = str(levels[0].get("url") or "stream.m3u8") if levels else "stream.m3u8"
        playlist = f"{address}/streams/{stream_id}/{leaf}"

    return StreamInfo(
        alias=alias,
        playlist_url=playlist,
        available=available,
        health=int(_f(quality.get("streamhealth")) or 0) if quality.get("streamhealth") is not None else None,
        motion_diff=_f(quality.get("motiondiff")),
        brightness=_f(quality.get("brightness")),
    )


def grab_frames(
    playlist_url: str,
    *,
    count: int = 4,
    interval_s: float = 2.0,
    timeout_s: float = 45.0,
) -> list[bytes]:
    """Capture `count` JPEG frames spaced `interval_s` apart via one ffmpeg call.

    Spacing frames out matters more than raw frame count: the pup moves, so
    independent samples give the detector several shots at a favourable pose and
    position instead of N near-identical images.
    """
    if not playlist_url:
        raise StreamUnavailable("empty_playlist_url")
    ffmpeg = os.environ.get("PUPWATCH_FFMPEG", "ffmpeg")
    # Capture a window long enough to contain `count` samples at 1/interval fps.
    duration = max(1.0, interval_s * count)
    with tempfile.TemporaryDirectory() as tmp:
        pattern = str(Path(tmp) / "f_%03d.jpg")
        argv = [
            ffmpeg,
            "-hide_banner",
            "-loglevel", "error",
            "-user_agent", _UA,
            "-i", playlist_url,
            "-t", f"{duration:.2f}",
            "-vf", f"fps=1/{interval_s:.3f}",
            "-frames:v", str(count),
            "-q:v", "3",
            pattern,
        ]
        try:
            proc = subprocess.run(
                argv, capture_output=True, timeout=timeout_s, check=False
            )
        except subprocess.TimeoutExpired as e:
            raise StreamUnavailable(f"ffmpeg_timeout after={timeout_s}s") from e
        frames = [p.read_bytes() for p in sorted(Path(tmp).glob("f_*.jpg"))]
        if not frames:
            err = (proc.stderr or b"").decode("utf-8", "replace")[:300]
            raise StreamUnavailable(f"ffmpeg_no_frames rc={proc.returncode} err={err}")
        log.info(
            "pup-watch frames_grabbed n=%d requested=%d bytes=%d",
            len(frames), count, sum(len(f) for f in frames),
        )
        return frames
