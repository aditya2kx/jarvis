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


if __name__ == "__main__":
    unittest.main()
