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


class _FakeEmailLoc:
    """Stub for ``.magic-link-sent__email``. ``recipient=None`` means the element
    is absent; ``""`` means present-but-blank (the soft-block); a string means a
    real deliverable recipient."""

    def __init__(self, recipient):
        self._r = recipient

    @property
    def first(self):
        return self

    def count(self):
        return 0 if self._r is None else 1

    def inner_text(self, timeout=None):
        return self._r or ""


class _FakePage:
    def __init__(self, *, body="", magic_count=0, recipient="adi@x.co"):
        self._body = body
        self._magic = magic_count
        self._recipient = recipient
        self.url = "https://app.squareup.com/login"
        self.goto_url = None

    def locator(self, selector):
        # Match the recipient selector BEFORE the generic "magic" check — the
        # selector ".magic-link-sent__email" also contains "magic".
        if selector == ".magic-link-sent__email":
            return _FakeEmailLoc(self._recipient)
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


class TestMagicLinkRecipient:
    def test_returns_recipient_when_present(self):
        page = _FakePage(recipient="alice@example.com")
        assert runner._magic_link_recipient(page) == "alice@example.com"

    def test_none_when_element_absent(self):
        assert runner._magic_link_recipient(_FakePage(recipient=None)) is None

    def test_none_when_blank(self):
        # The 2026-06-08 soft-block: element present but empty.
        assert runner._magic_link_recipient(_FakePage(recipient="")) is None

    def test_none_when_locator_raises(self):
        class _BoomPage:
            def locator(self, selector):
                raise RuntimeError("page closed")

        assert runner._magic_link_recipient(_BoomPage()) is None


class TestMagicLinkDeviceBlock:
    def test_blank_recipient_raises_without_prompting(self, monkeypatch):
        """Soft-block (blank recipient) must raise SquareDeviceBlockedError and
        NEVER prompt the operator for a paste that can't be satisfied."""
        import skills.slack.adapter as adapter

        called = {"reply": False}

        def _boom(**kw):
            called["reply"] = True
            return "should-not-be-asked"

        monkeypatch.setattr(adapter, "request_reply", _boom)
        page = _FakePage(body="Magic link sent.", recipient="")
        with pytest.raises(runner.SquareDeviceBlockedError, match="blank-recipient"):
            runner._handle_magic_link(page, store="palmetto")
        assert called["reply"] is False, "must not Slack-prompt on a device block"

    def test_absent_recipient_also_blocks(self, monkeypatch):
        import skills.slack.adapter as adapter
        monkeypatch.setattr(adapter, "request_reply",
                            lambda **kw: pytest.fail("must not prompt"))
        with pytest.raises(runner.SquareDeviceBlockedError):
            runner._handle_magic_link(_FakePage(recipient=None), store="palmetto")


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

    def test_extracts_url_from_surrounding_text(self, monkeypatch):
        monkeypatch.setattr(runner, "get_credentials", lambda store: {"username": "a"})
        import skills.slack.adapter as adapter
        monkeypatch.setattr(
            adapter, "request_reply",
            lambda **kw: "here: https://squareup.com/login?rml=1&token=ABC please",
        )
        page = _FakePage(body="Magic link sent.")
        runner._handle_magic_link(page, store="palmetto")
        assert page.goto_url == "https://squareup.com/login?rml=1&token=ABC"

    def test_accepts_app_subdomain(self, monkeypatch):
        monkeypatch.setattr(runner, "get_credentials", lambda store: {"username": "a"})
        import skills.slack.adapter as adapter
        url = "https://app.squareup.com/login?rml=1&token=ABC"
        monkeypatch.setattr(adapter, "request_reply", lambda **kw: url)
        page = _FakePage(body="Magic link sent.")
        runner._handle_magic_link(page, store="palmetto")
        assert page.goto_url == url


class TestDriveVerification:
    """The post-password challenge router + the single fresh-retry signal."""

    def test_blocked_on_attempt1_deletes_session_and_signals_retry(self, monkeypatch):
        import agents.bhaga.scripts.gcs_cache as gcs_cache
        deleted = {"n": 0}

        def _fake_delete(*, portal, store):
            deleted["n"] += 1
            assert (portal, store) == ("square", "palmetto")
            return True

        monkeypatch.setattr(gcs_cache, "delete_session", _fake_delete)
        monkeypatch.setattr(runner, "_is_magic_link_sent", lambda page: True)

        def _block(page, *, store):
            raise runner.SquareDeviceBlockedError("blank-recipient block")

        monkeypatch.setattr(runner, "_handle_magic_link", _block)
        with pytest.raises(runner._RetryFreshLogin):
            runner._drive_verification(_FakePage(), store="palmetto", attempt=1)
        assert deleted["n"] == 1

    def test_signals_retry_even_if_session_delete_errors(self, monkeypatch):
        """A GCS hiccup while discarding the poisoned session must not mask the
        retry signal — the inner delete is best-effort."""
        import agents.bhaga.scripts.gcs_cache as gcs_cache

        def _boom(*, portal, store):
            raise RuntimeError("GCS unavailable")

        monkeypatch.setattr(gcs_cache, "delete_session", _boom)
        monkeypatch.setattr(runner, "_is_magic_link_sent", lambda page: True)
        monkeypatch.setattr(
            runner, "_handle_magic_link",
            lambda page, *, store: (_ for _ in ()).throw(
                runner.SquareDeviceBlockedError("blank-recipient")),
        )
        with pytest.raises(runner._RetryFreshLogin):
            runner._drive_verification(_FakePage(), store="palmetto", attempt=1)

    def test_blocked_on_attempt2_propagates_no_retry(self, monkeypatch):
        import agents.bhaga.scripts.gcs_cache as gcs_cache
        monkeypatch.setattr(gcs_cache, "delete_session",
                            lambda **kw: pytest.fail("must not delete again on retry"))
        monkeypatch.setattr(runner, "_is_magic_link_sent", lambda page: True)
        monkeypatch.setattr(
            runner, "_handle_magic_link",
            lambda page, *, store: (_ for _ in ()).throw(
                runner.SquareDeviceBlockedError("still blocked")),
        )
        with pytest.raises(runner.SquareDeviceBlockedError):
            runner._drive_verification(_FakePage(), store="palmetto", attempt=2)

    def test_sms_path_when_no_magic_link(self, monkeypatch):
        """A fresh attempt that yields the SMS picker drives _handle_square_two_factor."""
        calls = {"sms": 0}
        monkeypatch.setattr(runner, "_is_magic_link_sent", lambda page: False)
        monkeypatch.setattr(runner, "_is_on_dashboard", lambda url: True)

        def _sms(page, *, store):
            calls["sms"] += 1

        monkeypatch.setattr(runner, "_handle_square_two_factor", _sms)
        runner._drive_verification(_FakePage(), store="palmetto", attempt=2)
        assert calls["sms"] == 1

    def test_deliverable_magic_link_relays_no_retry(self, monkeypatch):
        """A magic link WITH a recipient still relays (no block, no retry signal)."""
        monkeypatch.setattr(runner, "_is_magic_link_sent", lambda page: True)
        relayed = {"n": 0}

        def _relay(page, *, store):
            relayed["n"] += 1  # pretend the paste relay succeeded

        monkeypatch.setattr(runner, "_handle_magic_link", _relay)
        runner._drive_verification(_FakePage(recipient="adi@x.co"), store="palmetto", attempt=1)
        assert relayed["n"] == 1


class TestRedactUrlValues:
    def test_keeps_keys_redacts_values(self):
        out = runner._redact_url_values("https://squareup.com/login?rml=1&token=SECRET&uid=42")
        assert out == "https://squareup.com/login?rml=…&token=…&uid=…"
        assert "SECRET" not in out and "42" not in out

    def test_no_query(self):
        assert runner._redact_url_values("https://squareup.com/login") == "https://squareup.com/login"


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
