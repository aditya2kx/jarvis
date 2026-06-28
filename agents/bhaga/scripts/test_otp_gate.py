#!/usr/bin/env python3
"""Unit tests for the two-step OTP availability gate (otp_gate.py).

Covers the READY-reply matcher, the gate decision state machine (PROCEED /
EXIT_PENDING / SKIP_OTP), the 48h cap boundary, and the multi-portal "one
READY covers all" contract. All state access is injected, so no real backend,
Slack, or browser is touched.
"""

from __future__ import annotations

import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts import otp_gate
from agents.bhaga.scripts.otp_gate import (
    CT,
    EXIT_PENDING,
    PROCEED,
    SKIP_OTP,
    OtpWaitTimeout,
    PendingOtpAvailability,
    evaluate,
    is_ready_reply,
    portals_label,
)

REFRESH_DATE = datetime.date(2026, 5, 28)
NOW = datetime.datetime(2026, 5, 28, 21, 0, 0, tzinfo=CT)


# ── READY matcher ──────────────────────────────────────────────────


class TestIsReadyReply:
    def test_plain_ready_words(self):
        for w in ["ready", "READY", "ok", "Okay", "go", "yes", "yep", "available", "here", "y"]:
            assert is_ready_reply(w) is True, w

    def test_leading_token_match(self):
        assert is_ready_reply("ready to go") is True
        assert is_ready_reply("ok grabbing my phone") is True
        assert is_ready_reply("yes go ahead") is True

    def test_trailing_punctuation_and_emphasis(self):
        assert is_ready_reply("ready!") is True
        assert is_ready_reply("*ready*") is True
        assert is_ready_reply("ok.") is True

    def test_otp_code_is_not_ready(self):
        # An OTP code must NEVER be mistaken for a READY reply.
        for code in ["123456", "1234", "12-34-56", "  654321 "]:
            assert is_ready_reply(code) is False, code

    def test_unrelated_text_is_not_ready(self):
        for t in ["", "no", "later", "hold on", "status", "retry", None]:
            assert is_ready_reply(t) is False, t


# ── portals_label ──────────────────────────────────────────────────


class TestPortalsLabel:
    def test_one(self):
        assert portals_label(["Square"]) == "Square"

    def test_two(self):
        assert portals_label(["Square", "ADP"]) == "Square and ADP"

    def test_three(self):
        assert portals_label(["A", "B", "C"]) == "A, B, and C"

    def test_empty(self):
        assert portals_label([]) == ""


# ── Gate decision state machine ────────────────────────────────────


def _gate(portals, pending, *, now=NOW, cap_hours=48):
    return evaluate(REFRESH_DATE, portals, now=now, cap_hours=cap_hours,
                    get_pending=lambda _d: pending)


class TestAssumeReady:
    def test_assume_ready_proceeds_without_checkpoint(self, monkeypatch):
        # Operator-supervised live run: BHAGA_OTP_ASSUME_READY=1 drives OTP inline
        # (no checkpoint-and-resume), so a pending=None state still PROCEEDs.
        monkeypatch.setenv("BHAGA_OTP_ASSUME_READY", "1")
        decision, info = _gate(["Square"], None)
        assert decision == PROCEED
        assert "assume-ready" in info["reason"]

    def test_unset_inline_autostart_proceeds(self, monkeypatch):
        # Default (no env vars): inline-autostart mode → PROCEED immediately.
        monkeypatch.delenv("BHAGA_OTP_ASSUME_READY", raising=False)
        monkeypatch.delenv("BHAGA_OTP_REQUIRE_READY", raising=False)
        decision, info = _gate(["ADP"], None)
        assert decision == PROCEED
        assert "inline" in info["reason"]

    def test_require_ready_restores_checkpoint_behavior(self, monkeypatch):
        monkeypatch.delenv("BHAGA_OTP_ASSUME_READY", raising=False)
        monkeypatch.setenv("BHAGA_OTP_REQUIRE_READY", "1")
        decision, _ = _gate(["Square"], None)
        assert decision == EXIT_PENDING


class TestOtpWaitTimeout:
    def test_is_exception(self):
        exc = OtpWaitTimeout("timed out")
        assert isinstance(exc, Exception)
        assert "timed out" in str(exc)


class TestGateEvaluate:
    def test_zero_otp_portals_proceeds(self):
        # The zero-OTP happy path: nothing will launch a browser → PROCEED
        # without ever consulting a checkpoint or pinging the operator.
        decision, info = evaluate(REFRESH_DATE, [], get_pending=lambda _d: None)
        assert decision == PROCEED

    def test_default_inline_proceeds_without_checkpoint(self, monkeypatch):
        # Default mode: no env set → PROCEED immediately, no pending check.
        monkeypatch.delenv("BHAGA_OTP_ASSUME_READY", raising=False)
        monkeypatch.delenv("BHAGA_OTP_REQUIRE_READY", raising=False)
        decision, info = evaluate(REFRESH_DATE, ["ADP"],
                                  get_pending=lambda _d: (_ for _ in ()).throw(
                                      AssertionError("get_pending should not be called")))
        assert decision == PROCEED
        assert "inline" in info["reason"]

    def test_no_checkpoint_exits_pending_first_request(self, monkeypatch):
        monkeypatch.setenv("BHAGA_OTP_REQUIRE_READY", "1")
        decision, info = _gate(["Square"], None)
        assert decision == EXIT_PENDING
        assert info["first_request"] is True

    def test_outstanding_request_exits_pending_no_reping(self, monkeypatch):
        monkeypatch.setenv("BHAGA_OTP_REQUIRE_READY", "1")
        pending = {
            "portals": ["Square"],
            "requested_at": (NOW - datetime.timedelta(hours=3)).isoformat(),
            "ready_received": False,
        }
        decision, info = _gate(["Square"], pending)
        assert decision == EXIT_PENDING
        assert info["first_request"] is False

    def test_ready_received_proceeds(self, monkeypatch):
        monkeypatch.setenv("BHAGA_OTP_REQUIRE_READY", "1")
        pending = {
            "portals": ["Square", "ADP"],
            "requested_at": (NOW - datetime.timedelta(hours=2)).isoformat(),
            "ready_received": True,
            "ready_at": NOW.isoformat(),
        }
        decision, info = _gate(["Square", "ADP"], pending)
        assert decision == PROCEED
        # One READY covers ALL portals in the run.
        assert info["portals"] == ["Square", "ADP"]

    def test_48h_cap_not_yet_reached_exits_pending(self, monkeypatch):
        monkeypatch.setenv("BHAGA_OTP_REQUIRE_READY", "1")
        pending = {
            "portals": ["Square"],
            "requested_at": (NOW - datetime.timedelta(hours=47, minutes=59)).isoformat(),
            "ready_received": False,
        }
        decision, _ = _gate(["Square"], pending)
        assert decision == EXIT_PENDING

    def test_48h_cap_reached_skips_otp(self, monkeypatch):
        monkeypatch.setenv("BHAGA_OTP_REQUIRE_READY", "1")
        pending = {
            "portals": ["Square", "ADP"],
            "requested_at": (NOW - datetime.timedelta(hours=48)).isoformat(),
            "ready_received": False,
        }
        decision, info = _gate(["Square", "ADP"], pending)
        assert decision == SKIP_OTP
        assert info["portals"] == ["Square", "ADP"]

    def test_48h_cap_well_past_skips_otp(self, monkeypatch):
        monkeypatch.setenv("BHAGA_OTP_REQUIRE_READY", "1")
        pending = {
            "portals": ["ADP"],
            "requested_at": (NOW - datetime.timedelta(days=3)).isoformat(),
            "ready_received": False,
        }
        decision, _ = _gate(["ADP"], pending)
        assert decision == SKIP_OTP

    def test_naive_requested_at_is_tolerated(self, monkeypatch):
        # A naive (tz-less) timestamp must not raise; it's treated as CT.
        monkeypatch.setenv("BHAGA_OTP_REQUIRE_READY", "1")
        naive = (NOW - datetime.timedelta(hours=50)).replace(tzinfo=None)
        pending = {"portals": ["Square"], "requested_at": naive.isoformat(),
                   "ready_received": False}
        decision, _ = _gate(["Square"], pending)
        assert decision == SKIP_OTP


class TestPendingOtpAvailability:
    def test_carries_portals(self):
        exc = PendingOtpAvailability(["Square", "ADP"])
        assert exc.portals == ["Square", "ADP"]
        assert "Square" in str(exc) and "ADP" in str(exc)


class TestForceRequest:
    """force_request is only meaningful in BHAGA_OTP_REQUIRE_READY=1 mode."""

    def test_force_re_posts_on_stale_outstanding(self, monkeypatch):
        monkeypatch.setenv("BHAGA_OTP_REQUIRE_READY", "1")
        pending = {
            "portals": ["Square"],
            "requested_at": (NOW - datetime.timedelta(hours=3)).isoformat(),
            "ready_received": False,
        }
        decision, info = evaluate(
            REFRESH_DATE, ["Square"], now=NOW,
            get_pending=lambda _d: pending, force_request=True,
        )
        assert decision == EXIT_PENDING
        assert info["first_request"] is True

    def test_force_beats_48h_cap(self, monkeypatch):
        # Past the cap, force still re-posts (operator wants the data now).
        monkeypatch.setenv("BHAGA_OTP_REQUIRE_READY", "1")
        pending = {
            "portals": ["Square", "ADP"],
            "requested_at": (NOW - datetime.timedelta(days=3)).isoformat(),
            "ready_received": False,
        }
        decision, info = evaluate(
            REFRESH_DATE, ["Square", "ADP"], now=NOW,
            get_pending=lambda _d: pending, force_request=True,
        )
        assert decision == EXIT_PENDING
        assert info["first_request"] is True

    def test_force_still_proceeds_when_ready_received(self, monkeypatch):
        monkeypatch.setenv("BHAGA_OTP_REQUIRE_READY", "1")
        pending = {
            "portals": ["Square"],
            "requested_at": (NOW - datetime.timedelta(hours=2)).isoformat(),
            "ready_received": True,
            "ready_at": NOW.isoformat(),
        }
        decision, _ = evaluate(
            REFRESH_DATE, ["Square"], now=NOW,
            get_pending=lambda _d: pending, force_request=True,
        )
        assert decision == PROCEED

    def test_force_from_env(self, monkeypatch):
        monkeypatch.setenv("BHAGA_OTP_REQUIRE_READY", "1")
        monkeypatch.setenv("BHAGA_OTP_FORCE_REQUEST", "1")
        pending = {
            "portals": ["Square"],
            "requested_at": (NOW - datetime.timedelta(hours=3)).isoformat(),
            "ready_received": False,
        }
        # force_request omitted → resolved from env.
        decision, info = evaluate(
            REFRESH_DATE, ["Square"], now=NOW, get_pending=lambda _d: pending,
        )
        assert decision == EXIT_PENDING
        assert info["first_request"] is True

    def test_no_force_keeps_silent_outstanding(self, monkeypatch):
        monkeypatch.setenv("BHAGA_OTP_REQUIRE_READY", "1")
        monkeypatch.delenv("BHAGA_OTP_FORCE_REQUEST", raising=False)
        pending = {
            "portals": ["Square"],
            "requested_at": (NOW - datetime.timedelta(hours=3)).isoformat(),
            "ready_received": False,
        }
        decision, info = evaluate(
            REFRESH_DATE, ["Square"], now=NOW, get_pending=lambda _d: pending,
        )
        assert decision == EXIT_PENDING
        assert info["first_request"] is False
