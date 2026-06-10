"""Tests for scripts/pr_cost_store.py.

All BQ I/O is replaced by an in-memory fake so tests are fully offline.
Tests cover: pr_key(), _session_uid(), _review_uid(), and save/load/all_prs/delete
round-trip fidelity (meta, 2 build sessions, 1 review run).
"""
from __future__ import annotations

import hashlib
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Stub google.cloud.bigquery before importing pr_cost_store so the module
# can be imported without the real library installed.
# ---------------------------------------------------------------------------
_bq_stub = types.ModuleType("google.cloud.bigquery")


class _ScalarParam:
    def __init__(self, name, t, value):
        self.name = name
        self.t = t
        self.value = value


class _QueryJobConfig:
    def __init__(self, query_parameters=None):
        self.query_parameters = query_parameters or []


_bq_stub.ScalarQueryParameter = _ScalarParam
_bq_stub.QueryJobConfig = _QueryJobConfig


class _Dataset:
    def __init__(self, ref):
        self.location = "US"


_bq_stub.Dataset = _Dataset

google_mod = types.ModuleType("google")
google_cloud_mod = types.ModuleType("google.cloud")
google_mod.cloud = google_cloud_mod
google_cloud_mod.bigquery = _bq_stub
sys.modules.setdefault("google", google_mod)
sys.modules.setdefault("google.cloud", google_cloud_mod)
sys.modules.setdefault("google.cloud.bigquery", _bq_stub)
sys.modules.setdefault("google.oauth2", types.ModuleType("google.oauth2"))
sys.modules.setdefault("google.oauth2.credentials", types.ModuleType("google.oauth2.credentials"))

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pr_cost_store as S  # noqa: E402


# ---------------------------------------------------------------------------
# In-memory fake store
# ---------------------------------------------------------------------------

class _InMemoryRows:
    """Tiny in-memory store that mimics a table: list of dicts."""
    def __init__(self):
        self._rows: list[dict] = []

    def insert(self, row: dict) -> None:
        self._rows.append(dict(row))

    def select_where(self, col: str, val) -> list[dict]:
        return [r for r in self._rows if r.get(col) == val]

    def delete_where(self, col: str, val) -> None:
        self._rows = [r for r in self._rows if r.get(col) != val]

    def all(self) -> list[dict]:
        return list(self._rows)


def _make_fake_store():
    """Returns (pr_table, build_table, review_table, fake_client)."""
    pr_t = _InMemoryRows()
    build_t = _InMemoryRows()
    review_t = _InMemoryRows()
    tables = {S._T_PR: pr_t, S._T_BUILD: build_t, S._T_REVIEW: review_t}

    class FakeResult:
        def __init__(self, rows):
            self._rows = rows
        def __iter__(self):
            return iter(self._rows)
        def result(self):
            return self

    class FakeClient:
        def create_dataset(self, *a, **kw): pass
        def query(self, sql: str, job_config=None):
            # Route INSERTs and DELETEs to the right table
            s = sql.strip()
            if s.startswith("INSERT INTO"):
                for name, t in tables.items():
                    if name in s:
                        if job_config and hasattr(job_config, "query_parameters"):
                            row = {p.name: p.value for p in job_config.query_parameters}
                            t.insert(row)
                        break
                return FakeResult([])
            elif s.startswith("DELETE FROM"):
                for name, t in tables.items():
                    if name in s:
                        if job_config and hasattr(job_config, "query_parameters"):
                            for p in job_config.query_parameters:
                                if p.name == "k":
                                    t.delete_where("pr_key", p.value)
                        break
                return FakeResult([])
            elif s.startswith("MERGE"):
                # Extract params; upsert into pr_t
                if job_config and hasattr(job_config, "query_parameters"):
                    row = {p.name: p.value for p in job_config.query_parameters}
                    key = row.get("pr_key")
                    existing = pr_t.select_where("pr_key", key)
                    if existing:
                        pr_t.delete_where("pr_key", key)
                    pr_t.insert(row)
                return FakeResult([])
            elif "CREATE TABLE IF NOT EXISTS" in s or "CREATE OR REPLACE VIEW" in s:
                return FakeResult([])
            elif s.startswith("SELECT") or s.startswith("select"):
                # Route SELECT queries
                for name, t in tables.items():
                    if name in s:
                        if "WHERE pr_key=@k" in s:
                            if job_config and hasattr(job_config, "query_parameters"):
                                for p in job_config.query_parameters:
                                    if p.name == "k":
                                        rows = t.select_where("pr_key", p.value)
                                        return FakeResult([dict(r) for r in rows])
                        elif "WHERE pr_number IS NOT NULL" in s:
                            nums = sorted(set(
                                r["pr_number"] for r in t.all()
                                if r.get("pr_number") is not None
                            ))
                            return FakeResult([{"pr_number": n} for n in nums])
                        return FakeResult(t.all())
                return FakeResult([])
            elif s.startswith("TRUNCATE"):
                for name, t in tables.items():
                    if name in s:
                        t._rows.clear()
                return FakeResult([])
            return FakeResult([])

    return pr_t, build_t, review_t, FakeClient()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPrKey(unittest.TestCase):
    def test_integer(self):
        self.assertEqual(S.pr_key(47), "47")

    def test_string_numeric(self):
        self.assertEqual(S.pr_key("47"), "47")

    def test_string_branch(self):
        self.assertEqual(S.pr_key("feat/foo"), "branch:feat/foo")

    def test_int_leading_zero(self):
        self.assertEqual(S.pr_key(7), "7")


class TestSessionUid(unittest.TestCase):
    def test_stable(self):
        s = {"ts": "2026-01-01T00:00:00Z", "cost_usd": 1.23}
        uid1 = S._session_uid(s)
        uid2 = S._session_uid(s)
        self.assertEqual(uid1, uid2)
        self.assertEqual(len(uid1), 16)

    def test_different_sessions(self):
        a = {"ts": "2026-01-01T00:00:00Z", "cost_usd": 1.0}
        b = {"ts": "2026-01-02T00:00:00Z", "cost_usd": 2.0}
        self.assertNotEqual(S._session_uid(a), S._session_uid(b))


class TestReviewUid(unittest.TestCase):
    def test_uses_run_url(self):
        r = {"run_url": "https://example.com/123"}
        uid = S._review_uid(r)
        expected = hashlib.sha1(b"https://example.com/123").hexdigest()[:16]
        self.assertEqual(uid, expected)

    def test_stable(self):
        r = {"ts": "2026-01-01T00:00:00Z", "model": "claude"}
        self.assertEqual(S._review_uid(r), S._review_uid(r))


class TestSaveLoadRoundTrip(unittest.TestCase):
    def setUp(self):
        self.pr_t, self.build_t, self.review_t, self.fake_client = _make_fake_store()
        self._patcher = patch.object(S, "_client", return_value=self.fake_client)
        self._patcher.start()
        self._schema_patcher = patch.object(S, "ensure_schema")
        self._schema_patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self._schema_patcher.stop()

    def _make_rec(self, pr_number: int) -> dict:
        return {
            "pr_number": pr_number,
            "provisional_id": None,
            "title": f"Test PR {pr_number}",
            "requirement": "test requirement",
            "branch": f"feat/test-{pr_number}",
            "created_at": "2026-01-01T00:00:00Z",
            "merged_at": "2026-01-02T00:00:00Z",
            "session_started_at": None,
            "diff": {"files": 3, "additions": 10, "deletions": 5},
            "build": {
                "source": "test",
                "approximate": False,
                "attribution_mode": "conversation",
                "window": {"start": "2026-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z"},
                "conversation_ids": ["abc123"],
                "sessions": [
                    {"ts": "2026-01-01T01:00:00Z", "model": "claude-sonnet-4-6",
                     "tokens": 1000, "cost_usd": 0.50, "cost_source": "api",
                     "conversation_id": "abc123", "input_tokens": 500, "output_tokens": 500,
                     "cache_read_input_tokens": None, "cache_creation_input_tokens": None,
                     "note": None},
                    {"ts": "2026-01-01T02:00:00Z", "model": "claude-opus-4-8",
                     "tokens": 2000, "cost_usd": 1.50, "cost_source": "api",
                     "conversation_id": "abc123", "input_tokens": 800, "output_tokens": 1200,
                     "cache_read_input_tokens": 50, "cache_creation_input_tokens": None,
                     "note": "test note"},
                ],
                "cost_usd_total": 2.00,
                "tokens_total": 3000,
            },
            "review": {
                "source": "ci",
                "runs": [
                    {"ts": "2026-01-02T00:00:00Z", "model": "claude-sonnet-4-6",
                     "turns": 10, "input_tokens": 200, "output_tokens": 100,
                     "cache_read_input_tokens": None, "cache_creation_input_tokens": None,
                     "tokens": 300, "cost_usd": 0.05, "result": "approved",
                     "run_url": "https://github.com/test/pr/1"},
                ],
                "cost_usd_total": 0.05,
                "tokens_total": 300,
            },
        }

    def test_save_load_meta(self):
        rec = self._make_rec(100)
        S.save_record(rec)
        loaded = S.load_record(100, lambda pr: {"pr_number": pr, "build": {"sessions": []}, "review": {"runs": []}})
        self.assertEqual(loaded["title"], "Test PR 100")
        self.assertEqual(loaded["branch"], "feat/test-100")
        self.assertEqual(loaded["pr_number"], 100)

    def test_save_load_build_sessions(self):
        rec = self._make_rec(101)
        S.save_record(rec)
        loaded = S.load_record(
            101,
            lambda pr: {"pr_number": pr, "build": {"sessions": [], "conversation_ids": []}, "review": {"runs": []}},
        )
        self.assertEqual(len(loaded["build"]["sessions"]), 2)
        self.assertEqual(loaded["build"]["sessions"][0]["tokens"], 1000)
        self.assertAlmostEqual(loaded["build"]["sessions"][1]["cost_usd"], 1.50)

    def test_save_load_review_runs(self):
        rec = self._make_rec(102)
        S.save_record(rec)
        loaded = S.load_record(
            102,
            lambda pr: {"pr_number": pr, "build": {"sessions": []}, "review": {"runs": []}},
        )
        self.assertEqual(len(loaded["review"]["runs"]), 1)
        self.assertEqual(loaded["review"]["runs"][0]["result"], "approved")

    def test_all_prs_returns_numeric_only(self):
        S.save_record(self._make_rec(200))
        S.save_record(self._make_rec(201))
        prs = S.all_prs()
        self.assertIn(200, prs)
        self.assertIn(201, prs)

    def test_provisional_excluded_from_all_prs(self):
        rec = self._make_rec(300)
        rec["pr_number"] = None
        rec["provisional_id"] = "feat/my-branch"
        S.save_record(rec)
        prs = S.all_prs()
        self.assertNotIn(None, prs)
        # all_prs only queries WHERE pr_number IS NOT NULL
        self.assertTrue(all(isinstance(p, int) for p in prs))

    def test_delete_record(self):
        S.save_record(self._make_rec(400))
        S.delete_record(400)
        loaded = S.load_record(
            400, lambda pr: {"pr_number": None, "build": {"sessions": []}, "review": {"runs": []}}
        )
        self.assertIsNone(loaded["pr_number"])

    def test_load_missing_returns_empty_factory(self):
        result = S.load_record(9999, lambda pr: {"pr_number": pr, "marker": "empty"})
        self.assertEqual(result["marker"], "empty")


if __name__ == "__main__":
    unittest.main()
