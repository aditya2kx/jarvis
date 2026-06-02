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


def test_decodes_html_escaped_ampersands(monkeypatch):
    """REGRESSION: Slack returns `&` as `&amp;` in message text, so a magic link
    with multiple query params arrived as `?rml=1&amp;token=ABC&amp;uid=123`. The
    old unwrap kept the `&amp;`, corrupting the query string and making Square
    reject the link. request_reply must HTML-unescape so we get literal `&`."""
    _wire(
        monkeypatch,
        "<https://squareup.com/login?rml=1&amp;token=ABC&amp;uid=123|Sign in to Square>",
    )
    out = adapter.request_reply("U1", "paste the link", timeout_seconds=30, poll_interval=0)
    assert out == "https://squareup.com/login?rml=1&token=ABC&uid=123"
    assert "&amp;" not in out


def test_decodes_html_escaped_bare_url_with_surrounding_text(monkeypatch):
    _wire(monkeypatch, "here you go: https://squareup.com/login?a=1&amp;b=2 thanks")
    out = adapter.request_reply("U1", "paste the link", timeout_seconds=30, poll_interval=0)
    # _clean_slack_reply unescapes; the URL extraction (with surrounding words kept)
    # is the caller's job, but the ampersand must already be literal here.
    assert "&amp;" not in out
    assert "https://squareup.com/login?a=1&b=2" in out


def test_timeout_returns_none(monkeypatch):
    monkeypatch.setattr(adapter, "open_dm", lambda user_id, agent=None: "D1")
    monkeypatch.setattr(adapter, "send_message", lambda ch, msg, agent=None: {"ts": "1"})
    monkeypatch.setattr(adapter, "read_replies", lambda ch, oldest=None, limit=5: [])
    monkeypatch.setattr(adapter.time, "sleep", lambda s: None)
    # timeout_seconds=0 → loop body never runs → None
    assert adapter.request_reply("U1", "x", timeout_seconds=0, poll_interval=0) is None
