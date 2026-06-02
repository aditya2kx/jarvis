"""Tests for the sandbox scenario suite — comment parsing + committed-config
loading (the pure selection logic the workflow relies on)."""

from __future__ import annotations

import textwrap

import pytest

from agents.bhaga.scripts import sandbox_scenarios as sc


class TestParseComment:
    def test_parses_scenario_and_date(self):
        got = sc.parse_comment("/sandbox run item-sales-live date=2026-05-31")
        assert got == {"name": "item-sales-live", "date": "2026-05-31"}

    def test_parses_scenario_without_date(self):
        got = sc.parse_comment("/sandbox run full-live")
        assert got == {"name": "full-live", "date": None}

    def test_case_insensitive_command(self):
        assert sc.parse_comment("/SANDBOX RUN item-sales-live")["name"] == "item-sales-live"

    def test_unknown_scenario_is_none(self):
        assert sc.parse_comment("/sandbox run not-a-scenario") is None

    def test_non_command_is_none(self):
        assert sc.parse_comment("looks good to me 👍") is None
        assert sc.parse_comment("") is None


class TestLoadConfig:
    def test_loads_scenarios(self, tmp_path):
        p = tmp_path / "sandbox-live.yml"
        p.write_text(textwrap.dedent("""
            scenarios:
              - name: item-sales-live
                date: 2026-05-31
        """))
        plan = sc.load_config(str(p))
        assert plan == [{"name": "item-sales-live", "date": "2026-05-31"}]

    def test_missing_file_is_empty(self, tmp_path):
        assert sc.load_config(str(tmp_path / "nope.yml")) == []

    def test_empty_file_is_empty(self, tmp_path):
        p = tmp_path / "sandbox-live.yml"
        p.write_text("")
        assert sc.load_config(str(p)) == []

    def test_unknown_scenario_raises(self, tmp_path):
        p = tmp_path / "sandbox-live.yml"
        p.write_text("scenarios:\n  - name: bogus\n    date: 2026-05-31\n")
        with pytest.raises(ValueError, match="unknown sandbox scenario"):
            sc.load_config(str(p))


class TestRepoConfigIsValid:
    """The committed .github/sandbox-live.yml must reference real scenarios."""

    def test_repo_config_parses(self):
        import os
        root = os.path.dirname(os.path.abspath(__file__)).rsplit("/agents/", 1)[0]
        cfg = os.path.join(root, ".github", "sandbox-live.yml")
        plan = sc.load_config(cfg)
        for item in plan:
            assert item["name"] in sc.SCENARIOS
            assert item.get("date")
