#!/usr/bin/env python3
"""Tests for dev_models.py — the dev-flow model-routing single source of truth."""

import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dev_models as DM

REPO_ROOT = Path(__file__).resolve().parent.parent
COST_MD = REPO_ROOT / "docs" / "contributing" / "cost.md"


class TestConstants(unittest.TestCase):
    def test_every_routing_slug_has_a_friendly_name(self):
        for _task, slug in DM.ROUTING_TABLE:
            self.assertIn(slug, DM.FRIENDLY, f"missing FRIENDLY entry for {slug!r}")

    def test_default_impl_model_is_sonnet_5(self):
        self.assertEqual(DM.DEFAULT_IMPL_MODEL, "claude-sonnet-5-thinking-medium")


class TestRenderRoutingMd(unittest.TestCase):
    def test_contains_markers_and_all_rows(self):
        md = DM.render_routing_md()
        self.assertTrue(md.startswith(DM._ROUTING_MD_BEGIN))
        self.assertTrue(md.endswith(DM._ROUTING_MD_END))
        for task, slug in DM.ROUTING_TABLE:
            self.assertIn(task, md)
            self.assertIn(DM.FRIENDLY[slug], md)


class TestRenderRoutingReminder(unittest.TestCase):
    def test_contains_every_friendly_name(self):
        text = DM.render_routing_reminder()
        for _task, slug in DM.ROUTING_TABLE:
            self.assertIn(DM.FRIENDLY[slug], text)


class TestDocSync(unittest.TestCase):
    """Fails if docs/contributing/cost.md's generated block drifts from dev_models.py."""

    def test_cost_md_block_matches_generated_output(self):
        body = COST_MD.read_text(encoding="utf-8")
        m = re.search(
            re.escape(DM._ROUTING_MD_BEGIN) + r"(.*?)" + re.escape(DM._ROUTING_MD_END),
            body,
            re.DOTALL,
        )
        self.assertIsNotNone(m, "cost.md is missing the dev-models sync markers")
        doc_block = f"{DM._ROUTING_MD_BEGIN}{m.group(1)}{DM._ROUTING_MD_END}"
        self.assertEqual(
            doc_block.strip(),
            DM.render_routing_md().strip(),
            "docs/contributing/cost.md is out of sync with dev_models.py — "
            "run `python3 scripts/dev_models.py emit-routing-md` and paste the "
            "output between the markers.",
        )


class TestCli(unittest.TestCase):
    def test_emit_routing_md_prints_generated_block(self):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = DM.main(["emit-routing-md"])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().strip(), DM.render_routing_md().strip())


if __name__ == "__main__":
    unittest.main()
