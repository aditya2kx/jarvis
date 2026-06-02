"""Tests for the Square magic-link relay + trusted-device session helpers.

Covers the 2026-06-01 incident: an unrecognized device made Square escalate to
an email magic link ("Magic link sent. Use this device to sign in.") instead of
the SMS code, which the code-entry flow can't satisfy. The relay detects that
page and navigates to an operator-supplied URL in THIS browser."""

from __future__ import annotations

import pytest

from skills.square_tips import runner


class _FakeBody:
    def __init__(self, text):
        self._t = text

    def inner_text(self, timeout=None):
        return self._t


class _FakeCountLoc:
    def __init__(self, n):
        self._n = n

    def count(self):
        return self._n


class _FakePage:
    def __init__(self, *, body="", magic_count=0):
        self._body = body
        self._magic = magic_count
        self.url = "https://app.squareup.com/login"
        self.goto_url = None

    def locator(self, selector):
        if "magic" in selector:
            return _FakeCountLoc(self._magic)
        if selector == "body":
            return _FakeBody(self._body)
        return _FakeCountLoc(0)

    def goto(self, url, wait_until=None):
        self.goto_url = url
        self.url = "https://app.squareup.com/dashboard/home"

    def wait_for_function(self, js, timeout=None):
        return None

    def wait_for_timeout(self, ms):
        return None


class TestMagicLinkDetection:
    def test_detects_by_heading_text(self):
        page = _FakePage(body="Magic link sent. Use this device to sign in.")
        assert runner._is_magic_link_sent(page) is True

    def test_detects_by_marker_element(self):
        assert runner._is_magic_link_sent(_FakePage(magic_count=1)) is True

    def test_negative_on_normal_page(self):
        assert runner._is_magic_link_sent(_FakePage(body="Enter the code we texted you")) is False


class TestMagicLinkRelay:
    def test_navigates_to_pasted_url(self, monkeypatch):
        monkeypatch.setattr(runner, "get_credentials",
                            lambda store: {"username": "adi@x.co", "password": "p"})
        import skills.slack.adapter as adapter
        url = "https://squareup.com/login?rml=1&p=person%3AABC&v=737238"
        monkeypatch.setattr(adapter, "request_reply", lambda **kw: url)
        page = _FakePage(body="Magic link sent.")
        runner._handle_magic_link(page, store="palmetto")
        assert page.goto_url == url

    def test_rejects_non_square_url(self, monkeypatch):
        monkeypatch.setattr(runner, "get_credentials", lambda store: {"username": "a"})
        import skills.slack.adapter as adapter
        monkeypatch.setattr(adapter, "request_reply", lambda **kw: "https://evil.example/login?x=1")
        with pytest.raises(RuntimeError, match="not a Square login URL"):
            runner._handle_magic_link(_FakePage(), store="palmetto")

    def test_raises_when_operator_times_out(self, monkeypatch):
        monkeypatch.setattr(runner, "get_credentials", lambda store: {"username": "a"})
        import skills.slack.adapter as adapter
        monkeypatch.setattr(adapter, "request_reply", lambda **kw: None)
        with pytest.raises(RuntimeError, match="did not paste the link"):
            runner._handle_magic_link(_FakePage(), store="palmetto")


class TestSandboxRunLabel:
    def test_prefix_only_for_sandbox(self, monkeypatch):
        monkeypatch.delenv("BHAGA_RUN_ENV", raising=False)
        assert runner._run_label_prefix() == ""
        monkeypatch.setenv("BHAGA_RUN_ENV", "sandbox")
        monkeypatch.setenv("BHAGA_RUN_LABEL", "PR#9 fix")
        assert "SANDBOX" in runner._run_label_prefix() and "PR#9 fix" in runner._run_label_prefix()


class TestSessionPersistence:
    def test_disabled_returns_none(self, monkeypatch):
        monkeypatch.delenv("BHAGA_SESSION_PERSIST", raising=False)
        assert runner.restore_session_path("palmetto") is None

    def test_enabled_restores_from_gcs(self, monkeypatch):
        monkeypatch.setenv("BHAGA_SESSION_PERSIST", "1")
        import agents.bhaga.scripts.gcs_cache as gcs_cache

        def fake_download(dest, *, portal, store):
            dest.write_text("{}")  # pretend a session existed
            return True

        monkeypatch.setattr(gcs_cache, "download_session", fake_download)
        path = runner.restore_session_path("palmetto")
        assert path == runner._SESSION_TMP

    def test_persist_noop_when_disabled(self, monkeypatch):
        monkeypatch.delenv("BHAGA_SESSION_PERSIST", raising=False)
        calls = []

        class _Ctx:
            def storage_state(self, path=None):
                calls.append(path)

        runner.persist_session(_Ctx(), "palmetto")  # should not touch the context
        assert calls == []
