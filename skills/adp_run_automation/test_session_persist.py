#!/usr/bin/env python3
"""Tests for ADP trusted-device session reuse (Issue #305).

`gcs_cache.upload_session` / `download_session` and `launch_persistent`'s
`storage_state` parameter all existed but had ZERO callers, so every ADP login
started from a fresh cookie jar and was fully exposed whenever ADP's risk engine
challenged. These cover the wiring that makes the next run a trusted device.
"""

from __future__ import annotations

import contextlib
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.adp_run_automation import runner


class TestRestoreAdpSession(unittest.TestCase):
    def test_disabled_without_the_env_flag(self):
        with mock.patch.dict(os.environ, {"BHAGA_SESSION_PERSIST": ""}):
            self.assertIsNone(runner._restore_adp_session(store="palmetto"))

    def test_returns_local_path_on_a_cache_hit(self):
        with mock.patch.dict(os.environ, {"BHAGA_SESSION_PERSIST": "1"}), \
             mock.patch("agents.bhaga.scripts.gcs_cache.download_session",
                        return_value=True):
            self.assertEqual(
                runner._restore_adp_session(store="palmetto"),
                str(runner.ADP_SESSION_LOCAL),
            )

    def test_returns_none_on_a_cache_miss(self):
        """A miss must mean a fresh jar and a full login, never a bogus path —
        launch_persistent would otherwise be handed a nonexistent file."""
        with mock.patch.dict(os.environ, {"BHAGA_SESSION_PERSIST": "1"}), \
             mock.patch("agents.bhaga.scripts.gcs_cache.download_session",
                        return_value=False):
            self.assertIsNone(runner._restore_adp_session(store="palmetto"))


class TestPersistAdpSession(unittest.TestCase):
    def test_saves_then_uploads(self):
        ctx = mock.Mock()
        with mock.patch.dict(os.environ, {"BHAGA_SESSION_PERSIST": "1"}), \
             mock.patch("agents.bhaga.scripts.gcs_cache.upload_session") as up:
            runner._persist_adp_session(ctx, store="palmetto")
        ctx.storage_state.assert_called_once_with(
            path=str(runner.ADP_SESSION_LOCAL),
        )
        up.assert_called_once()
        self.assertEqual(up.call_args.kwargs["portal"], "adp")
        self.assertEqual(up.call_args.kwargs["store"], "palmetto")

    def test_disabled_without_the_env_flag(self):
        ctx = mock.Mock()
        with mock.patch.dict(os.environ, {"BHAGA_SESSION_PERSIST": ""}), \
             mock.patch("agents.bhaga.scripts.gcs_cache.upload_session") as up:
            runner._persist_adp_session(ctx, store="palmetto")
        ctx.storage_state.assert_not_called()
        up.assert_not_called()

    def test_storage_state_failure_never_fails_the_run(self):
        """The scrape's data has already landed by this point; losing the
        session costs one extra login, not the run."""
        ctx = mock.Mock()
        ctx.storage_state.side_effect = RuntimeError("context closed")
        with mock.patch.dict(os.environ, {"BHAGA_SESSION_PERSIST": "1"}), \
             mock.patch("agents.bhaga.scripts.gcs_cache.upload_session"):
            runner._persist_adp_session(ctx, store="palmetto")

    def test_upload_failure_never_fails_the_run(self):
        ctx = mock.Mock()
        with mock.patch.dict(os.environ, {"BHAGA_SESSION_PERSIST": "1"}), \
             mock.patch("agents.bhaga.scripts.gcs_cache.upload_session",
                        side_effect=RuntimeError("GCS down")):
            runner._persist_adp_session(ctx, store="palmetto")


class TestAdpSession(unittest.TestCase):
    """``adp_session`` is the single ADP entry point.

    Restore/persist used to be wired only into ``download_adp_bundle``, so the
    payroll draft, pay-info scrape and payroll-home dump each paid their own 2FA
    SMS. The sequence now lives in one context manager.
    """

    def _drive(self):
        """Enter/exit adp_session with the browser and login stubbed out."""
        ctx, page = mock.Mock(), mock.Mock()
        calls: list[str] = []

        @contextlib.contextmanager
        def fake_launch(**kwargs):
            calls.append(f"launch:{kwargs.get('storage_state')}")
            yield ctx, page

        with mock.patch.object(runner, "launch_persistent", fake_launch), \
             mock.patch.object(runner, "_restore_adp_session",
                               side_effect=lambda **k: calls.append("restore") or "/tmp/s.json"), \
             mock.patch.object(runner, "_ensure_logged_in",
                               side_effect=lambda *a, **k: calls.append("login")), \
             mock.patch.object(runner, "_persist_adp_session",
                               side_effect=lambda *a, **k: calls.append("persist")):
            with runner.adp_session(store="palmetto", headed=False) as (c, p):
                calls.append("body")
                self.assertIs(c, ctx)
                self.assertIs(p, page)
        return calls

    def test_restores_then_logs_in_then_persists_before_the_body(self):
        calls = self._drive()
        self.assertEqual(
            calls,
            ["restore", "launch:/tmp/s.json", "login", "persist", "body"],
        )

    def test_persists_before_the_body_so_a_partial_run_still_leaves_trust(self):
        """Persisting at block exit would lose the jar whenever the caller's
        body raises — which is exactly when a retry is about to need it."""
        ctx, page = mock.Mock(), mock.Mock()

        @contextlib.contextmanager
        def fake_launch(**kwargs):
            yield ctx, page

        with mock.patch.object(runner, "launch_persistent", fake_launch), \
             mock.patch.object(runner, "_restore_adp_session", return_value=None), \
             mock.patch.object(runner, "_ensure_logged_in"), \
             mock.patch.object(runner, "_persist_adp_session") as persist:
            with self.assertRaises(RuntimeError):
                with runner.adp_session(store="palmetto", headed=False):
                    raise RuntimeError("scrape blew up")
        persist.assert_called_once()


class TestEveryAdpEntryPointIsWired(unittest.TestCase):
    """Mechanical gate: no ADP browser may be launched outside ``adp_session``.

    A reviewer cannot be expected to notice a new ``launch_persistent(portal="adp")``
    that forgets restore/persist; the symptom is only an extra OTP SMS at 3am. This
    fails the build instead.
    """

    SKILL_DIR = os.path.dirname(os.path.abspath(__file__))

    def test_no_direct_adp_launch_outside_the_session_helper(self):
        offenders: list[str] = []
        for name in sorted(os.listdir(self.SKILL_DIR)):
            if not name.endswith(".py") or name.startswith("test_"):
                continue
            path = os.path.join(self.SKILL_DIR, name)
            with open(path) as fh:
                lines = fh.readlines()
            for i, line in enumerate(lines, start=1):
                if 'portal="adp"' not in line:
                    continue
                # Legitimate uses: the session helper's own launch, and the
                # GCS upload/download of the jar (portal is a path segment).
                window = "".join(lines[max(0, i - 40):i])
                if "def adp_session" in window or "_session(ADP_SESSION_LOCAL" in line:
                    continue
                offenders.append(f"{name}:{i}: {line.strip()}")
        self.assertEqual(
            offenders, [],
            "launch ADP through runner.adp_session() so the trusted-device jar is "
            "restored and re-saved; direct launches pay a fresh 2FA SMS:\n"
            + "\n".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()


class TestOtpWaitNeverOutlivesThePasscodePage(unittest.TestCase):
    """ADP's passcode page dies before our old 30-minute wait did.

    Live 2026-09-21: the operator replied at ~15 minutes, we accepted the code and
    submitted it into a page that had already replaced itself with "Your session
    has timed out due to inactivity". The code was spent, the run was lost, and
    the DM had promised 30 minutes. Waiting longer than the portal allows cannot
    succeed, so it must not be offered.
    """

    def _src(self) -> str:
        import inspect

        from skills.adp_run_automation import runner

        return inspect.getsource(runner._handle_adp_2fa_challenge) if hasattr(
            runner, "_handle_adp_2fa_challenge"
        ) else inspect.getsource(runner)

    def test_the_wait_is_capped_under_the_ten_minute_validity(self):
        src = self._src()
        self.assertIn("ADP_PASSCODE_TTL_S = 600", src)
        self.assertIn("ADP_PASSCODE_TTL_S - 60", src)

    def test_the_env_override_can_only_shorten_the_wait(self):
        """A stale BHAGA_OTP_WAIT_S=1800 in a job must not reinstate the bug."""
        src = self._src()
        self.assertIn("wait_s = min(", src)


class TestTimecardCanBeForcedAfterAMidDayPunchEdit(unittest.TestCase):
    """Same-day caching assumes punches only change overnight. They don't.

    Live 2026-09-21: the operator fixed a missing punch around midday, a
    "refresh" reused the 09:33 file, and BigQuery stayed at 42.68h while the
    payroll grid read 43.43h. The download must be forceable.
    """

    def _src(self) -> str:
        import inspect

        from skills.adp_run_automation import runner

        return inspect.getsource(runner.download_timecard)

    def test_download_timecard_takes_force(self):
        import inspect

        from skills.adp_run_automation import runner

        sig = inspect.signature(runner.download_timecard)
        self.assertIn("force", sig.parameters)
        self.assertIs(sig.parameters["force"].default, False)

    def test_force_removes_the_cached_file_before_the_freshness_check(self):
        src = self._src()
        cut = src.index("_xlsx_fresh_for_target")
        self.assertIn("if force and expected.exists():", src[:cut])
        self.assertIn("expected.unlink()", src[:cut])

    def test_the_cli_threads_force_to_timecard(self):
        import inspect

        from skills.adp_run_automation import runner

        src = inspect.getsource(runner)
        block = src[src.index('if args.scrape == "timecard":'):]
        self.assertIn("force=args.force,", block[: block.index("elif")])
