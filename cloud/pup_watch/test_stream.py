"""Playlist resolution from the camera alias, and frame-grab failure handling."""

import json

import pytest

from cloud.pup_watch import stream

# Trimmed from a real getcamerastreamstate.php response for alias 5ee276849d4bf.
LIVE_PAYLOAD = {
    "details": {
        "alias": "5ee276849d4bf",
        "streamavailable": "1",
        "streamid": "4fbtg1pein2lst7ca",
        "address": "http://s79.ipcamlive.com/",
    },
    "streaminfo": {
        "video": {"width": "720", "height": "576", "fps": "25.00"},
        "live": {"levels": [{"width": "0", "height": "0", "url": "stream.m3u8"}]},
        "quality": {"streamhealth": "97", "motiondiff": "0.08", "brightness": "0.46"},
    },
}


def _patch_get(monkeypatch, payload):
    monkeypatch.setattr(
        stream, "_get", lambda url, referer, timeout=15.0: json.dumps(payload).encode()
    )


def test_resolve_builds_https_playlist_from_alias(monkeypatch):
    _patch_get(monkeypatch, LIVE_PAYLOAD)
    info = stream.resolve_stream("5ee276849d4bf")
    assert info.usable is True
    # ipcamlive advertises http; we must not pull video in plaintext.
    assert info.playlist_url == "https://s79.ipcamlive.com/streams/4fbtg1pein2lst7ca/stream.m3u8"
    assert info.health == 97
    assert info.motion_diff == pytest.approx(0.08)


def test_resolve_honours_advertised_playlist_leaf(monkeypatch):
    payload = json.loads(json.dumps(LIVE_PAYLOAD))
    payload["streaminfo"]["live"]["levels"] = [{"url": "stream_L2000.m3u8"}]
    _patch_get(monkeypatch, payload)
    assert stream.resolve_stream("x").playlist_url.endswith("/stream_L2000.m3u8")


def test_unavailable_camera_is_not_usable(monkeypatch):
    payload = json.loads(json.dumps(LIVE_PAYLOAD))
    payload["details"]["streamavailable"] = "0"
    _patch_get(monkeypatch, payload)
    info = stream.resolve_stream("x")
    assert info.available is False
    assert info.usable is False


def test_missing_stream_id_yields_no_playlist(monkeypatch):
    payload = json.loads(json.dumps(LIVE_PAYLOAD))
    payload["details"]["streamid"] = ""
    _patch_get(monkeypatch, payload)
    assert stream.resolve_stream("x").usable is False


def test_network_failure_raises_stream_unavailable(monkeypatch):
    def boom(url, referer, timeout=15.0):
        raise OSError("connection reset")

    monkeypatch.setattr(stream, "_get", boom)
    with pytest.raises(stream.StreamUnavailable):
        stream.resolve_stream("x")


def test_garbage_json_raises_stream_unavailable(monkeypatch):
    monkeypatch.setattr(stream, "_get", lambda url, referer, timeout=15.0: b"<html>nope")
    with pytest.raises(stream.StreamUnavailable):
        stream.resolve_stream("x")


def test_grab_frames_rejects_empty_playlist():
    with pytest.raises(stream.StreamUnavailable):
        stream.grab_frames("")


def test_grab_frames_raises_when_ffmpeg_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("PUPWATCH_FFMPEG", "/bin/true")
    with pytest.raises(stream.StreamUnavailable) as e:
        stream.grab_frames("https://example.invalid/stream.m3u8", count=2, timeout_s=5)
    assert "ffmpeg_no_frames" in str(e.value)
