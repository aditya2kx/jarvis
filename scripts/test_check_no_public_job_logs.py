"""Unit tests for scripts/check_no_public_job_logs.py."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_no_public_job_logs as gate  # noqa: E402


class NoPublicJobLogsGateTests(unittest.TestCase):
    def test_clean_prod_ops_snippet_is_ok(self):
        clean = 'print("prod-ops: job logs → `/prod-read logs --job x`")\n'
        self.assertEqual(gate.violations(Path("scripts/prod_ops.py"), clean), [])

    def test_logging_read_is_flagged(self):
        bad = 'subprocess.run(["gcloud", "logging", "read", filt])\n'
        hits = gate.violations(Path("scripts/prod_ops.py"), bad)
        self.assertEqual(len(hits), 1)
        self.assertIn("public Actions must not dump job logs", hits[0])

    def test_text_payload_is_flagged(self):
        hits = gate.violations(Path("w.yml"), "echo ${{ steps.x.outputs.textPayload }}\n")
        self.assertEqual(len(hits), 1)

    def test_main_passes_on_current_tree(self):
        self.assertEqual(gate.main(), 0)

    def test_main_fails_when_prod_ops_dumps_logs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "scripts").mkdir()
            (root / ".github" / "workflows").mkdir(parents=True)
            prod = root / "scripts" / "prod_ops.py"
            prod.write_text('os.system("gcloud logging read filter")\n')
            (root / ".github" / "workflows" / "x.yml").write_text("name: x\n")
            with mock.patch.object(gate, "_REPO", root), \
                    mock.patch.object(
                        gate, "_SCAN",
                        (prod, root / ".github" / "workflows" / "x.yml"),
                    ):
                self.assertEqual(gate.main(), 1)


if __name__ == "__main__":
    unittest.main()
