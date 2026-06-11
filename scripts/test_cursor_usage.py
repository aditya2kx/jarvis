#!/usr/bin/env python3
"""Tests for cursor_usage pure helpers."""

import base64
import datetime
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cursor_usage as U


class TestToMs(unittest.TestCase):
    def test_iso_z(self):
        expected = int(
            datetime.datetime(2026, 6, 3, 4, 0, tzinfo=datetime.timezone.utc).timestamp() * 1000
        )
        self.assertEqual(U.to_ms("2026-06-03T04:00:00Z"), expected)

    def test_int_passthrough(self):
        self.assertEqual(U.to_ms(1748923200000), 1748923200000)

    def test_digit_string(self):
        self.assertEqual(U.to_ms("1748923200000"), 1748923200000)


class TestParseGitDate(unittest.TestCase):
    def test_cursor_scored_commit_format(self):
        dt = U._parse_git_date("Tue Jun 2 13:26:27 2026 -0500")
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 6)
        self.assertEqual(dt.day, 2)


class TestJwtClaims(unittest.TestCase):
    def test_decodes_payload(self):
        payload = base64.urlsafe_b64encode(
            json.dumps({"sub": "auth0|abc", "exp": 9999999999}).encode()
        ).decode().rstrip("=")
        tok = f"hdr.{payload}.sig"
        claims = U._jwt_claims(tok)
        self.assertEqual(claims["sub"], "auth0|abc")

    def test_malformed_token_raises_cursor_usage_error(self):
        with self.assertRaises(U.CursorUsageError):
            U._jwt_claims("not-a-jwt")
        with self.assertRaises(U.CursorUsageError):
            U._jwt_claims("hdr.@@@notbase64@@@.sig")


class TestDeriveWindowForBranch(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = Path(self._tmpdir.name) / "ai-code-tracking.db"
        con = sqlite3.connect(self.db)
        con.executescript("""
            CREATE TABLE scored_commits (
                commitHash TEXT NOT NULL, branchName TEXT NOT NULL,
                scoredAt INTEGER NOT NULL, commitDate TEXT,
                PRIMARY KEY (commitHash, branchName)
            );
            CREATE TABLE ai_code_hashes (
                hash TEXT PRIMARY KEY, source TEXT NOT NULL,
                timestamp INTEGER, model TEXT, createdAt INTEGER NOT NULL
            );
        """)
        con.execute(
            "INSERT INTO scored_commits VALUES (?, ?, ?, ?)",
            ("abc", "feat/x", 1, "Tue Jun 2 13:26:27 2026 -0500"),
        )
        # edit at 18:00 UTC on Jun 2 2026
        edit_ms = int(
            datetime.datetime(2026, 6, 2, 18, 0, tzinfo=datetime.timezone.utc).timestamp() * 1000
        )
        con.execute(
            "INSERT INTO ai_code_hashes VALUES (?, ?, ?, ?, ?)",
            ("h1", "composer", edit_ms, "composer-2.5", edit_ms),
        )
        con.commit()
        con.close()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_anchors_to_ai_edits(self):
        edit_ms = int(
            datetime.datetime(2026, 6, 2, 18, 0, tzinfo=datetime.timezone.utc).timestamp() * 1000
        )
        start_ms, end_ms = U.derive_window_for_branch(
            "feat/x", merged_at="2026-06-02T20:00:00Z", db=self.db,
        )
        self.assertLess(start_ms, edit_ms)
        self.assertGreater(end_ms, edit_ms)

    def test_clamps_lower_bound_to_prior_branch_commit(self):
        # Regression for the PR #14 double-count: a prior branch's commit at 18:00Z
        # precedes this branch's first commit (18:26Z). An AI edit at 17:00Z (older
        # than the prior commit) must NOT pull the window back into that prior PR.
        with sqlite3.connect(self.db) as con:
            con.execute(
                "INSERT INTO scored_commits VALUES (?, ?, ?, ?)",
                ("old", "old/y", 1, "Tue Jun 2 13:00:00 2026 -0500"),  # 18:00Z
            )
            early = int(datetime.datetime(2026, 6, 2, 17, 0, tzinfo=datetime.timezone.utc).timestamp() * 1000)
            late = int(datetime.datetime(2026, 6, 2, 18, 10, tzinfo=datetime.timezone.utc).timestamp() * 1000)
            con.execute("INSERT INTO ai_code_hashes VALUES (?, ?, ?, ?, ?)", ("e1", "composer", early, "m", early))
            con.execute("INSERT INTO ai_code_hashes VALUES (?, ?, ?, ?, ?)", ("e2", "composer", late, "m", late))
        start_ms, _ = U.derive_window_for_branch("feat/x", merged_at="2026-06-02T20:00:00Z", db=self.db)
        prior_ms = int(datetime.datetime(2026, 6, 2, 18, 0, tzinfo=datetime.timezone.utc).timestamp() * 1000)
        self.assertGreaterEqual(start_ms, prior_ms)  # clamped to the prior commit
        self.assertGreater(start_ms, early)          # the 17:00Z edit is excluded

    def test_falls_back_to_commit_bounded_window_when_no_ai_edits(self):
        # Branch with scored commits but ZERO ai_code_hashes rows in range:
        # the window must fall back to (first_commit - pre_buffer, merged_at).
        with sqlite3.connect(self.db) as con:
            con.execute("DELETE FROM ai_code_hashes")
        commit = U._parse_git_date("Tue Jun 2 13:26:27 2026 -0500")
        pre_buffer_min = 120
        merged_at = "2026-06-02T20:00:00Z"
        start_ms, end_ms = U.derive_window_for_branch(
            "feat/x", merged_at=merged_at, pre_buffer_min=pre_buffer_min, db=self.db,
        )
        expected_lo = int((commit - datetime.timedelta(minutes=pre_buffer_min)).timestamp() * 1000)
        expected_hi = int(
            datetime.datetime(2026, 6, 2, 20, 0, tzinfo=datetime.timezone.utc).timestamp() * 1000
        )
        self.assertEqual(start_ms, expected_lo)
        self.assertEqual(end_ms, expected_hi)


class TestSessionCookie(unittest.TestCase):
    def _make_token(self, sub: str, exp: int | None = None) -> str:
        claims: dict = {"sub": sub}
        if exp is not None:
            claims["exp"] = exp
        payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        return f"hdr.{payload}.sig"

    def test_formats_cookie(self):
        tok = self._make_token("auth0|user123", exp=9999999999)
        cookie = U._session_cookie(tok)
        self.assertIn("WorkosCursorSessionToken=user123::", cookie)

    def test_raises_on_expired_token(self):
        tok = self._make_token("auth0|u", exp=1)
        with self.assertRaises(U.CursorUsageError):
            U._session_cookie(tok)

    def test_raises_when_user_id_empty(self):
        tok = self._make_token("", exp=9999999999)
        with self.assertRaises(U.CursorUsageError):
            U._session_cookie(tok)


class TestResolveEventCostCents(unittest.TestCase):
    def test_cursor_charged_when_positive(self):
        cents, src = U.resolve_event_cost_cents({
            "chargedCents": 150,
            "cursorTokenFee": 5,
            "tokenUsage": {"totalCents": 100},
        })
        self.assertEqual(cents, 150)
        self.assertEqual(src, "cursor_charged")

    def test_byok_fallback_when_charged_zero(self):
        cents, src = U.resolve_event_cost_cents({
            "chargedCents": 0,
            "cursorTokenFee": 0,
            "tokenUsage": {"totalCents": 206.457275390625},
        })
        self.assertAlmostEqual(cents, 206.457275390625)
        self.assertEqual(src, "byok_token_usage")

    def test_byok_includes_cursor_token_fee(self):
        cents, src = U.resolve_event_cost_cents({
            "chargedCents": 0,
            "cursorTokenFee": 2.5,
            "tokenUsage": {"totalCents": 10},
        })
        self.assertEqual(cents, 12.5)
        self.assertEqual(src, "byok_token_usage")

    def test_zero_when_no_cost_fields(self):
        cents, src = U.resolve_event_cost_cents({"chargedCents": 0, "tokenUsage": {}})
        self.assertEqual(cents, 0.0)
        self.assertEqual(src, "zero")


class TestFetchUsageEvents(unittest.TestCase):
    """Mock the HTTP/pagination loop — no live Cursor session needed."""

    def _resp(self, payload: dict):
        from unittest import mock
        cm = mock.MagicMock()
        cm.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        return cm

    def _event(self, ts_ms: int, model: str, cents: int | None, inp: int = 10, outp: int = 5,
               *, total_cents: float | None = None):
        ev = {
            "timestamp": str(ts_ms), "model": model, "chargedCents": cents,
            "cursorTokenFee": 0, "isHeadless": False, "kind": "code",
            "tokenUsage": {"inputTokens": inp, "outputTokens": outp,
                           "cacheReadTokens": 0, "cacheWriteTokens": 0},
        }
        if total_cents is not None:
            ev["tokenUsage"]["totalCents"] = total_cents
        return ev

    def test_byok_event_uses_total_cents(self):
        from unittest import mock
        page = {"usageEventsDisplay": [
            self._event(1000, "claude-opus-4-8", 0, total_cents=206.45),
        ]}
        with mock.patch.object(U, "_read_access_token", return_value="t"), \
             mock.patch.object(U, "_session_cookie", return_value="c"), \
             mock.patch.object(U.urllib.request, "urlopen",
                               side_effect=[self._resp(page), self._resp({"usageEventsDisplay": []})]):
            events = U.fetch_usage_events(0, 9999)
        self.assertAlmostEqual(events[0]["cost_usd"], 2.0645)
        self.assertEqual(events[0]["cost_source"], "byok_token_usage")

    def test_paginates_dedups_and_sorts(self):
        from unittest import mock
        page1 = {"usageEventsDisplay": [
            self._event(2000, "claude-opus", 100),
            self._event(1000, "claude-sonnet", 50),
            self._event(2000, "claude-opus", 100),  # exact page-overlap dup
        ]}
        page2 = {"usageEventsDisplay": [self._event(3000, "composer", 0)]}
        page3 = {"usageEventsDisplay": []}
        with mock.patch.object(U, "_read_access_token", return_value="t"), \
             mock.patch.object(U, "_session_cookie", return_value="c"), \
             mock.patch.object(U.urllib.request, "urlopen",
                               side_effect=[self._resp(page1), self._resp(page2), self._resp(page3)]):
            events = U.fetch_usage_events(0, 9999, page_size=3)  # full page → fetch next
        self.assertEqual([e["ts_ms"] for e in events], [1000, 2000, 3000])  # sorted, dup dropped
        self.assertEqual(events[1]["cost_usd"], 1.0)  # 100 cents
        self.assertEqual(events[0]["tokens"], 15)

    def test_keeps_distinct_same_ms_events_with_none_cents(self):
        from unittest import mock
        page = {"usageEventsDisplay": [
            self._event(5000, "m", None, inp=10),
            self._event(5000, "m", None, inp=20),  # same ts/model/cents, different tokens
        ]}
        with mock.patch.object(U, "_read_access_token", return_value="t"), \
             mock.patch.object(U, "_session_cookie", return_value="c"), \
             mock.patch.object(U.urllib.request, "urlopen", side_effect=[self._resp(page), self._resp({"usageEventsDisplay": []})]):
            events = U.fetch_usage_events(0, 9999, page_size=200)
        self.assertEqual(len(events), 2)

    def test_stops_on_short_page(self):
        from unittest import mock
        page = {"usageEventsDisplay": [self._event(1000, "m", 10)]}
        urlopen = mock.MagicMock(side_effect=[self._resp(page)])
        with mock.patch.object(U, "_read_access_token", return_value="t"), \
             mock.patch.object(U, "_session_cookie", return_value="c"), \
             mock.patch.object(U.urllib.request, "urlopen", urlopen):
            events = U.fetch_usage_events(0, 9999, page_size=200)
        self.assertEqual(len(events), 1)
        self.assertEqual(urlopen.call_count, 1)  # short page → no second request


class TestConversationAttribution(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.db = Path(self._tmpdir) / "ai.db"
        con = sqlite3.connect(self.db)
        con.execute(
            "create table ai_code_hashes (hash text, conversationId text, model text, timestamp integer)"
        )
        # conv-a: composer edits 1000-2000; conv-b: sonnet edits 1500-2500 (overlap)
        con.executemany(
            "insert into ai_code_hashes values (?,?,?,?)",
            [
                ("h1", "conv-a", "composer-2.5", 1000),
                ("h2", "conv-a", "composer-2.5", 1800),
                ("h3", "conv-a", "composer-2.5", 2000),
                ("h4", "conv-b", "claude-sonnet-4-6", 1500),
                ("h5", "conv-b", "claude-sonnet-4-6", 2500),
            ],
        )
        con.commit()
        con.close()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_auto_bind_single_dominant(self):
        ids = U.auto_bind_conversations(900, 2600, db=self.db, min_edits=1)
        self.assertEqual(len(ids), 1)

    def test_filter_by_model_tier(self):
        # conv-a used only composer — sonnet event at same timestamp should still drop.
        events = [
            {"ts_ms": 1800, "model": "composer-2.5", "tokens": 100, "cost_usd": 0.1},
            {"ts_ms": 1800, "model": "claude-sonnet-4-6", "tokens": 200, "cost_usd": 0.2},
        ]
        out = U.filter_events_for_conversations(events, ["conv-a"], 900, 2600, db=self.db)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["model"], "composer-2.5")
        self.assertEqual(out[0]["conversation_id"], "conv-a")

    def test_filter_keeps_all_tiers_for_mixed_conversation(self):
        """A conversation that used Opus (planning) AND Sonnet (execution) must keep
        events from both tiers.  The old dominant-model filter dropped the minority
        tier via an arbitrary string-length comparison (sonnet > opus in chars)."""
        tmpdir2 = tempfile.mkdtemp()
        try:
            db2 = Path(tmpdir2) / "ai.db"
            con = sqlite3.connect(db2)
            con.execute(
                "create table ai_code_hashes (hash text, conversationId text, model text, timestamp integer)"
            )
            con.executemany(
                "insert into ai_code_hashes values (?,?,?,?)",
                [
                    # conv-mixed: Opus planning early, Sonnet execution later
                    ("h1", "conv-mixed", "claude-opus-4-8", 1000),
                    ("h2", "conv-mixed", "claude-opus-4-8", 1200),
                    ("h3", "conv-mixed", "claude-sonnet-4-6", 1600),
                    ("h4", "conv-mixed", "claude-sonnet-4-6", 1900),
                ],
            )
            con.commit()
            con.close()

            events = [
                {"ts_ms": 1100, "model": "claude-opus-4-8",   "tokens": 50000, "cost_usd": 1.25},
                {"ts_ms": 1700, "model": "claude-sonnet-4-6", "tokens": 30000, "cost_usd": 0.45},
                # out-of-tier event (haiku) — should still be dropped
                {"ts_ms": 1500, "model": "claude-haiku-3",    "tokens": 5000,  "cost_usd": 0.01},
            ]
            out = U.filter_events_for_conversations(events, ["conv-mixed"], 900, 2100, db=db2)

            models_kept = {e["model"] for e in out}
            self.assertIn("claude-opus-4-8",   models_kept, "Opus planning event was dropped")
            self.assertIn("claude-sonnet-4-6", models_kept, "Sonnet execution event was dropped")
            self.assertNotIn("claude-haiku-3", models_kept, "Out-of-tier haiku event should be dropped")
            self.assertEqual(len(out), 2)
            self.assertTrue(all(e["conversation_id"] == "conv-mixed" for e in out))
        finally:
            import shutil
            shutil.rmtree(tmpdir2, ignore_errors=True)

    def test_model_in_conversation_helper(self):
        """Unit test _model_in_conversation directly."""
        self.assertTrue(U._model_in_conversation("claude-opus-4-8",   ["claude-opus-4-8", "claude-sonnet-4-6"]))
        self.assertTrue(U._model_in_conversation("claude-sonnet-4-6", ["claude-opus-4-8", "claude-sonnet-4-6"]))
        self.assertFalse(U._model_in_conversation("claude-haiku-3",   ["claude-opus-4-8", "claude-sonnet-4-6"]))
        # Empty conv_models passes through (no-profile fallback)
        self.assertTrue(U._model_in_conversation("claude-opus-4-8", []))


class TestWindowFromTranscript(unittest.TestCase):
    def test_brackets_timestamps_with_pads(self):
        import tempfile
        import os
        # Two events 10 minutes apart (600_000 ms)
        ts1 = "2026-06-10T10:00:00Z"
        ts2 = "2026-06-10T10:10:00Z"
        lines = [
            json.dumps({"timestamp": ts1}),
            json.dumps({"timestamp": ts2}),
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(lines) + "\n")
            path = f.name
        try:
            start_ms, end_ms = U.window_from_transcript(
                transcript_path=path, lead_pad_min=10, tail_pad_min=5
            )
            t1_ms = U.to_ms(ts1)
            t2_ms = U.to_ms(ts2)
            self.assertEqual(start_ms, t1_ms - 10 * 60_000)
            self.assertEqual(end_ms, t2_ms + 5 * 60_000)
        finally:
            os.unlink(path)

    def test_raises_when_no_timestamps(self):
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"no_ts": "here"}) + "\n")
            path = f.name
        try:
            with self.assertRaises(U.CursorUsageError):
                U.window_from_transcript(transcript_path=path)
        finally:
            os.unlink(path)

    def test_raises_when_no_conversation_id_and_no_path(self):
        with self.assertRaises(U.CursorUsageError):
            U.window_from_transcript()


if __name__ == "__main__":
    unittest.main()
