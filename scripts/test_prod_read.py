"""Unit tests for scripts/prod_read.py — no GCP; uses the local openssl binary."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from prod_read import (  # noqa: E402
    RequestError,
    _log_filter,
    check_select_only,
    decrypt,
    encrypt,
    keygen,
    parse_comment,
)

PUB = "QUJD"


class SelectOnlyTests(unittest.TestCase):
    def test_select_and_with_pass(self):
        check_select_only("SELECT 1")
        check_select_only("  with t as (select 1) select * from t;")

    def test_writes_rejected(self):
        for sql in (
            "DELETE FROM bhaga.x WHERE true",
            "SELECT 1; DROP TABLE bhaga.x",
            "WITH t AS (SELECT 1) INSERT INTO bhaga.x SELECT * FROM t",
            "MERGE bhaga.x USING y ON true WHEN MATCHED THEN DELETE",
        ):
            with self.assertRaises(RequestError, msg=sql):
                check_select_only(sql)

    def test_keywords_inside_strings_and_identifiers_are_fine(self):
        check_select_only("SELECT 'please update me' AS note, `delete` FROM t")

    def test_comment_cannot_hide_a_second_statement_start(self):
        with self.assertRaises(RequestError):
            check_select_only("/* SELECT */ UPDATE t SET a = 1")


class ParseCommentTests(unittest.TestCase):
    def test_status(self):
        req = parse_comment(f"/prod-read status --date 2026-10-10\npubkey: {PUB}")
        self.assertEqual(req, {"op": "status", "pubkey": PUB, "store": "palmetto", "date": "2026-10-10"})

    def test_bq_reads_fenced_sql(self):
        req = parse_comment(f"/prod-read bq\n```sql\nSELECT 1 AS x\n```\npubkey: {PUB}")
        self.assertEqual(req["sql"], "SELECT 1 AS x")

    def test_logs_defaults_and_validation(self):
        req = parse_comment(f"/prod-read logs --job bhaga-daily-refresh\npubkey: {PUB}")
        self.assertEqual((req["job"], req["since"]), ("bhaga-daily-refresh", "1h"))
        for bad in ("--service 'x\" OR true'", "--service a --job b", "--service a --since forever"):
            with self.assertRaises(RequestError, msg=bad):
                parse_comment(f"/prod-read logs {bad}\npubkey: {PUB}")

    def test_pubkey_required(self):
        with self.assertRaises(RequestError):
            parse_comment("/prod-read status")

    def test_unknown_op(self):
        with self.assertRaises(RequestError):
            parse_comment(f"/prod-read write-things\npubkey: {PUB}")

    def test_log_filter_scopes_to_the_named_resource(self):
        f = _log_filter({"service": "operator-console", "grep": "dedupe"})
        self.assertIn('resource.labels.service_name="operator-console"', f)
        self.assertTrue(f.endswith(' AND "dedupe"'))


class EncryptionTests(unittest.TestCase):
    def test_round_trip_only_with_the_private_key(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            pub = keygen(Path(a))
            keygen(Path(b))
            bundle = encrypt(b'{"rows": [1, 2]}', pub)
            self.assertNotIn("rows", json.dumps(bundle))
            self.assertEqual(decrypt(bundle, Path(a) / "key.pem"), b'{"rows": [1, 2]}')
            with self.assertRaises(RuntimeError):
                decrypt(bundle, Path(b) / "key.pem")


if __name__ == "__main__":
    unittest.main()
