"""Tests for BHAGA notify helpers — sandbox/PR labeling + evidence surfacing."""

from __future__ import annotations

import pytest

from agents.bhaga import notify


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    for var in ("BHAGA_RUN_ENV", "BHAGA_RUN_LABEL", "BHAGA_SLACK_DISABLED"):
        monkeypatch.delenv(var, raising=False)


class TestRunPrefix:
    def test_prod_prefix_is_empty(self):
        assert notify._run_prefix() == ""

    def test_sandbox_prefix_labels_env_and_pr(self, monkeypatch):
        monkeypatch.setenv("BHAGA_RUN_ENV", "sandbox")
        monkeypatch.setenv("BHAGA_RUN_LABEL", "PR#42 fix/item-sales")
        prefix = notify._run_prefix()
        assert "SANDBOX" in prefix
        assert "PR#42 fix/item-sales" in prefix

    def test_sandbox_prefix_without_label(self, monkeypatch):
        monkeypatch.setenv("BHAGA_RUN_ENV", "sandbox")
        prefix = notify._run_prefix()
        assert "SANDBOX" in prefix


class TestSafeSendLabeling:
    def test_safe_send_prepends_sandbox_label(self, monkeypatch):
        monkeypatch.setenv("BHAGA_RUN_ENV", "sandbox")
        monkeypatch.setenv("BHAGA_RUN_LABEL", "PR#42")
        sent = {}
        monkeypatch.setattr(notify, "send_message",
                            lambda channel, text, agent=None: sent.setdefault("text", text))
        monkeypatch.setattr(notify, "_resolve_dm_channel", lambda: "D_TEST")
        notify._safe_send("hello world")
        assert sent["text"].startswith(":test_tube: *[SANDBOX · PR#42]* ")
        assert sent["text"].endswith("hello world")


class TestFailureAlertEvidence:
    def test_includes_evidence_uri(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(notify, "_safe_send",
                            lambda text: captured.setdefault("text", text))
        notify.failure_alert(
            step="square",
            exception=RuntimeError("Item Sales page date picker not found"),
            date="2026-05-31",
            evidence_uri="gs://bhaga-scrape-cache/2026-05-31/evidence/",
        )
        assert "Evidence:" in captured["text"]
        assert "gs://bhaga-scrape-cache/2026-05-31/evidence/" in captured["text"]

    def test_omits_evidence_line_when_absent(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(notify, "_safe_send",
                            lambda text: captured.setdefault("text", text))
        notify.failure_alert(step="square", exception=RuntimeError("boom"), date="2026-05-31")
        assert "Evidence:" not in captured["text"]


class TestSquareDeviceBlockedAlert:
    def test_actionable_and_no_paste_instruction(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(notify, "_safe_send",
                            lambda text: captured.setdefault("text", text))
        notify.square_device_blocked_alert(
            date="2026-06-08",
            evidence_uri="gs://bhaga-scrape-cache/2026-06-08/evidence/",
        )
        text = captured["text"]
        # States the truth: undeliverable, nothing to paste, auto-retry.
        assert "undeliverable magic link" in text
        assert "nothing to paste" in text.lower()
        assert "auto-retry" in text
        assert "2026-06-08" in text
        assert "gs://bhaga-scrape-cache/2026-06-08/evidence/" in text
        # Must NOT tell the operator to paste a URL (the dead-end we removed).
        assert "paste it here" not in text.lower()
        assert "squareup.com/login?" not in text

    def test_omits_evidence_when_absent(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(notify, "_safe_send",
                            lambda text: captured.setdefault("text", text))
        notify.square_device_blocked_alert(date="2026-06-08")
        assert "Evidence:" not in captured["text"]
