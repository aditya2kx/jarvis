"""Tests for adapter.request_reply — the free-form reply path used to relay a
Square magic-link URL (request_otp only extracts numeric codes)."""

from __future__ import annotations

import skills.slack.adapter as adapter


def _wire(monkeypatch, reply_text):
    monkeypatch.setattr(adapter, "open_dm", lambda user_id, agent=None: "D1")
    sent = {}
    monkeypatch.setattr(adapter, "send_message",
                        lambda ch, msg, agent=None: sent.setdefault("ts", {"ts": "1"}) or {"ts": "1"})
    monkeypatch.setattr(adapter, "read_replies",
                        lambda ch, oldest=None, limit=5: [{"ts": "2", "user": "U1", "text": reply_text}])
    monkeypatch.setattr(adapter.time, "sleep", lambda s: None)


def test_unwraps_slack_linked_url(monkeypatch):
    _wire(monkeypatch, "<https://squareup.com/login?rml=1&v=737238|Sign in to Square>")
    out = adapter.request_reply("U1", "paste the link", timeout_seconds=30, poll_interval=0)
    assert out == "https://squareup.com/login?rml=1&v=737238"


def test_returns_bare_url(monkeypatch):
    _wire(monkeypatch, "https://squareup.com/login?rml=1")
    out = adapter.request_reply("U1", "paste the link", timeout_seconds=30, poll_interval=0)
    assert out == "https://squareup.com/login?rml=1"


def test_timeout_returns_none(monkeypatch):
    monkeypatch.setattr(adapter, "open_dm", lambda user_id, agent=None: "D1")
    monkeypatch.setattr(adapter, "send_message", lambda ch, msg, agent=None: {"ts": "1"})
    monkeypatch.setattr(adapter, "read_replies", lambda ch, oldest=None, limit=5: [])
    monkeypatch.setattr(adapter.time, "sleep", lambda s: None)
    # timeout_seconds=0 → loop body never runs → None
    assert adapter.request_reply("U1", "x", timeout_seconds=0, poll_interval=0) is None
