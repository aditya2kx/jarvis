#!/usr/bin/env python3
"""Integration tests for the OTP availability gate inside daily_refresh.main().

These drive ``main()`` with a patched boundary (no Sheets / Playwright / Slack /
GCS) far enough to exercise the gate decision and its side effects:

  * EXIT_PENDING — no prior READY → posts ONE READY request, persists the
    checkpoint, and exits cleanly (0) WITHOUT running any pipeline.
  * PROCEED      — READY present → proceeds, serializes the (multiple) OTP
    portals, and uses the short bounded code wait.
  * SKIP_OTP     — 48h elapsed → skips ONLY the OTP portals, finishes the rest,
    posts an alert, exits success.
  * Zero-OTP     — nothing will launch a browser → NO READY request posted.

A fresh-download / browser launch is NEVER triggered: the pipeline executor is
stubbed and the will-launch predicates are injected.
"""

from __future__ import annotations

import contextlib
import datetime
import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts import daily_refresh, otp_gate

PREV_END = datetime.date(2026, 5, 18)
REFRESH_ISO = "2026-05-20"  # past date → completeness gate passes unconditionally
PROFILE = {"calibration": {"first_data_window": {"start": "2026-03-22"}}}


class _StopAfterGate(Exception):
    """Raised by the stubbed pipeline executor to halt main() after the gate."""


@contextlib.contextmanager
def _patched_main(
    *,
    pending,
    square_launch=True,
    adp_launch=True,
    execute_stub=None,
    extra_argv=None,
):
    argv = ["daily_refresh", "--store", "palmetto", "--date", REFRESH_ISO, "--no-slack"]
    if extra_argv:
        argv += extra_argv

    recorded = {}

    def _save(refresh_date, portals, **kw):
        recorded["save"] = {"portals": list(portals), **kw}

    if execute_stub is None:
        def execute_stub(specs, *, serialize_otp):
            recorded["execute"] = {"specs": sorted(specs.keys()), "serialize_otp": serialize_otp}
            raise _StopAfterGate()

    es = contextlib.ExitStack()
    p = es.enter_context
    p(mock.patch.object(sys, "argv", argv))
    p(mock.patch.object(daily_refresh, "_load_profile", return_value=PROFILE))
    p(mock.patch.object(daily_refresh, "resolve_sheet_id", return_value="SID"))
    p(mock.patch.object(daily_refresh, "_read_data_window_end_from_sheet",
                        return_value=(PREV_END, False)))
    p(mock.patch.object(daily_refresh, "step_already_done", return_value=False))
    p(mock.patch.object(daily_refresh, "download_cached_files", return_value=[]))
    p(mock.patch.object(daily_refresh, "_square_will_launch_browser", return_value=square_launch))
    p(mock.patch.object(daily_refresh, "_adp_will_launch_browser", return_value=adp_launch))
    p(mock.patch("skills.bhaga_config.state_adapter.get_pending_otp", return_value=pending))
    p(mock.patch.object(daily_refresh, "_adapter_save_pending_otp", side_effect=_save))
    ready = p(mock.patch.object(daily_refresh, "ready_request"))
    skipped = p(mock.patch.object(daily_refresh, "otp_skipped_alert"))
    clear = p(mock.patch.object(daily_refresh, "_adapter_clear_pending_otp"))
    p(mock.patch.object(daily_refresh, "_execute_pipelines", side_effect=execute_stub))
    p(mock.patch.object(daily_refresh, "info_ping"))
    p(mock.patch.object(daily_refresh, "success_heartbeat"))
    try:
        yield {
            "recorded": recorded,
            "ready_request": ready,
            "otp_skipped_alert": skipped,
            "clear_pending": clear,
        }
    finally:
        es.close()


@pytest.fixture(autouse=True)
def _clean_otp_env(monkeypatch):
    monkeypatch.delenv("BHAGA_OTP_WAIT_S", raising=False)
    monkeypatch.delenv("REFRESH_DATE", raising=False)


def test_exit_pending_posts_request_checkpoints_and_exits_clean():
    """No prior READY → post request, checkpoint, exit 0, run NO pipeline."""
    with _patched_main(pending=None) as ctx:
        rc = daily_refresh.main()
    assert rc == 0
    # ONE READY request covering BOTH portals.
    ctx["ready_request"].assert_called_once()
    _, kw = ctx["ready_request"].call_args
    assert kw["portals"] == ["Square", "ADP"]
    # Checkpoint persisted with both portals.
    assert ctx["recorded"]["save"]["portals"] == ["Square", "ADP"]
    # No pipeline executed.
    assert "execute" not in ctx["recorded"]


def test_outstanding_request_exits_without_repinging():
    pending = {
        "portals": ["Square", "ADP"],
        "requested_at": (datetime.datetime.now(otp_gate.CT)
                         - datetime.timedelta(hours=2)).isoformat(),
        "ready_received": False,
    }
    with _patched_main(pending=pending) as ctx:
        rc = daily_refresh.main()
    assert rc == 0
    ctx["ready_request"].assert_not_called()
    assert "save" not in ctx["recorded"]


def test_proceed_after_ready_serializes_and_uses_short_wait():
    """One READY covers multiple OTP portals; they run back-to-back."""
    pending = {
        "portals": ["Square", "ADP"],
        "requested_at": (datetime.datetime.now(otp_gate.CT)
                         - datetime.timedelta(hours=1)).isoformat(),
        "ready_received": True,
        "ready_at": datetime.datetime.now(otp_gate.CT).isoformat(),
    }
    with _patched_main(pending=pending) as ctx:
        with pytest.raises(_StopAfterGate):
            daily_refresh.main()
    # Operator already confirmed → no new READY ping.
    ctx["ready_request"].assert_not_called()
    # Both OTP portals scheduled, serialized (back-to-back).
    assert set(["square", "adp"]).issubset(set(ctx["recorded"]["execute"]["specs"]))
    assert ctx["recorded"]["execute"]["serialize_otp"] is True
    # Short bounded code wait engaged.
    assert os.environ.get("BHAGA_OTP_WAIT_S") == "900"


def test_proceed_single_portal_does_not_serialize():
    pending = {
        "portals": ["Square"],
        "requested_at": datetime.datetime.now(otp_gate.CT).isoformat(),
        "ready_received": True,
    }
    with _patched_main(pending=pending, adp_launch=False) as ctx:
        with pytest.raises(_StopAfterGate):
            daily_refresh.main()
    assert ctx["recorded"]["execute"]["serialize_otp"] is False


def test_48h_cap_skips_otp_finishes_rest_and_alerts():
    pending = {
        "portals": ["Square", "ADP"],
        "requested_at": (datetime.datetime.now(otp_gate.CT)
                         - datetime.timedelta(days=3)).isoformat(),
        "ready_received": False,
    }
    # Skip downstream-heavy steps so main() can run to a clean success without
    # touching Sheets; the OTP portals are skipped by the gate itself.
    def _noop_execute(specs, *, serialize_otp):
        return {}

    with _patched_main(
        pending=pending,
        execute_stub=_noop_execute,
        extra_argv=["--skip-reviews", "--skip-model"],
    ) as ctx:
        rc = daily_refresh.main()
    assert rc == 0
    ctx["otp_skipped_alert"].assert_called_once()
    ctx["clear_pending"].assert_called()
    # No READY re-ping when we give up at the cap.
    ctx["ready_request"].assert_not_called()


def test_zero_otp_happy_path_posts_no_ready_request():
    """Cache/markers satisfy the steps → browser never launches → no READY."""
    with _patched_main(pending=None, square_launch=False, adp_launch=False) as ctx:
        with pytest.raises(_StopAfterGate):
            daily_refresh.main()
    ctx["ready_request"].assert_not_called()
    assert "save" not in ctx["recorded"]
    # Pipelines still scheduled (they'll no-op via freshness), but no gate ping.
    assert "execute" in ctx["recorded"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
