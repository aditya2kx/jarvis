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


class TestGateEvaluate:
    def test_zero_otp_portals_proceeds(self):
        # The zero-OTP happy path: nothing will launch a browser → PROCEED
        # without ever consulting a checkpoint or pinging the operator.
        decision, info = evaluate(REFRESH_DATE, [], get_pending=lambda _d: None)
        assert decision == PROCEED

    def test_no_checkpoint_exits_pending_first_request(self):
        decision, info = _gate(["Square"], None)
        assert decision == EXIT_PENDING
        assert info["first_request"] is True

    def test_outstanding_request_exits_pending_no_reping(self):
        pending = {
            "portals": ["Square"],
            "requested_at": (NOW - datetime.timedelta(hours=3)).isoformat(),
            "ready_received": False,
        }
        decision, info = _gate(["Square"], pending)
        assert decision == EXIT_PENDING
        assert info["first_request"] is False

    def test_ready_received_proceeds(self):
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

    def test_48h_cap_not_yet_reached_exits_pending(self):
        pending = {
            "portals": ["Square"],
            "requested_at": (NOW - datetime.timedelta(hours=47, minutes=59)).isoformat(),
            "ready_received": False,
        }
        decision, _ = _gate(["Square"], pending)
        assert decision == EXIT_PENDING

    def test_48h_cap_reached_skips_otp(self):
        pending = {
            "portals": ["Square", "ADP"],
            "requested_at": (NOW - datetime.timedelta(hours=48)).isoformat(),
            "ready_received": False,
        }
        decision, info = _gate(["Square", "ADP"], pending)
        assert decision == SKIP_OTP
        assert info["portals"] == ["Square", "ADP"]

    def test_48h_cap_well_past_skips_otp(self):
        pending = {
            "portals": ["ADP"],
            "requested_at": (NOW - datetime.timedelta(days=3)).isoformat(),
            "ready_received": False,
        }
        decision, _ = _gate(["ADP"], pending)
        assert decision == SKIP_OTP

    def test_naive_requested_at_is_tolerated(self):
        # A naive (tz-less) timestamp must not raise; it's treated as CT.
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
