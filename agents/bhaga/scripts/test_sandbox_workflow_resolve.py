"""Tests for the workflow resolver — decides run/skip + the plan from event env."""

from __future__ import annotations

import importlib

import pytest

resolve = importlib.import_module("agents.bhaga.scripts.sandbox_workflow_resolve")


def _outputs(tmp_path, monkeypatch, env: dict) -> dict:
    out_file = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
    for k in ("EVENT_NAME", "IN_SCENARIO", "IN_DATE", "IN_PR", "DISPATCH_REF",
              "PR_NUMBER", "PR_HEAD_REF", "PR_IS_FORK", "PR_HAS_LABEL",
              "CMT_BODY", "CMT_IS_PR", "CMT_ASSOC", "ISSUE_NUMBER"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    resolve.main()
    result = {}
    for line in out_file.read_text().splitlines():
        if "=" in line:
            key, val = line.split("=", 1)
            result[key] = val
    return result


class TestDispatch:
    def test_valid_scenario_runs(self, tmp_path, monkeypatch):
        out = _outputs(tmp_path, monkeypatch, {
            "EVENT_NAME": "workflow_dispatch",
            "IN_SCENARIO": "item-sales-live", "IN_DATE": "2026-05-31",
            "IN_PR": "9", "DISPATCH_REF": "main",
        })
        assert out["run"] == "true"
        assert out["pr_number"] == "9"
        assert '"item-sales-live"' in out["plan"]
        assert '"2026-05-31"' in out["plan"]

    def test_unknown_scenario_skips(self, tmp_path, monkeypatch):
        out = _outputs(tmp_path, monkeypatch, {
            "EVENT_NAME": "workflow_dispatch", "IN_SCENARIO": "bogus",
            "IN_DATE": "2026-05-31", "IN_PR": "9",
        })
        assert out["run"] == "false"


class TestPullRequest:
    def test_skips_without_label(self, tmp_path, monkeypatch):
        out = _outputs(tmp_path, monkeypatch, {
            "EVENT_NAME": "pull_request", "PR_IS_FORK": "false",
            "PR_HAS_LABEL": "false", "PR_NUMBER": "9", "PR_HEAD_REF": "feat/x",
        })
        assert out["run"] == "false"

    def test_skips_fork(self, tmp_path, monkeypatch):
        out = _outputs(tmp_path, monkeypatch, {
            "EVENT_NAME": "pull_request", "PR_IS_FORK": "true",
            "PR_HAS_LABEL": "true", "PR_NUMBER": "9", "PR_HEAD_REF": "feat/x",
        })
        assert out["run"] == "false"


class TestComment:
    def test_unauthorized_author_skips(self, tmp_path, monkeypatch):
        out = _outputs(tmp_path, monkeypatch, {
            "EVENT_NAME": "issue_comment", "CMT_IS_PR": "true",
            "CMT_ASSOC": "NONE", "CMT_BODY": "/sandbox run item-sales-live",
            "ISSUE_NUMBER": "9",
        })
        assert out["run"] == "false"

    def test_non_command_skips(self, tmp_path, monkeypatch):
        out = _outputs(tmp_path, monkeypatch, {
            "EVENT_NAME": "issue_comment", "CMT_IS_PR": "true",
            "CMT_ASSOC": "OWNER", "CMT_BODY": "lgtm", "ISSUE_NUMBER": "9",
        })
        assert out["run"] == "false"
