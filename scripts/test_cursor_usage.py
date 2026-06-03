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


class TestFetchUsageEvents(unittest.TestCase):
    """Mock the HTTP/pagination loop — no live Cursor session needed."""

    def _resp(self, payload: dict):
        from unittest import mock
        cm = mock.MagicMock()
        cm.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        return cm

    def _event(self, ts_ms: int, model: str, cents: int, inp: int = 10, outp: int = 5):
        return {
            "timestamp": str(ts_ms), "model": model, "chargedCents": cents,
            "isHeadless": False, "kind": "code",
            "tokenUsage": {"inputTokens": inp, "outputTokens": outp,
                           "cacheReadTokens": 0, "cacheWriteTokens": 0},
        }

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


if __name__ == "__main__":
    unittest.main()
