"""Unit tests for check_pr_review_replies._check_issue_comments."""
from __future__ import annotations

import sys
import types
from pathlib import Path

# Import without triggering __main__ execution.
src = Path(__file__).parent / "check_pr_review_replies.py"
spec = __import__("importlib.util", fromlist=["spec_from_file_location", "module_from_spec"])
import importlib.util

spec_ = importlib.util.spec_from_file_location("check_pr_review_replies", src)
mod = importlib.util.module_from_spec(spec_)
spec_.loader.exec_module(mod)

_check_issue_comments = mod._check_issue_comments


def _make_comment(id_: int, login: str) -> dict:
    return {"id": id_, "user": {"login": login}, "body": f"comment {id_}"}


def _run(comments: list[dict]) -> list[str]:
    """Call _check_issue_comments with a patched _gh_json that returns comments."""
    original = mod._gh_json
    mod._gh_json = lambda *_: comments
    try:
        return _check_issue_comments("owner/repo", 1)
    finally:
        mod._gh_json = original


def test_operator_no_reply_fails():
    comments = [_make_comment(1, "aditya2kx")]
    errors = _run(comments)
    assert len(errors) == 1
    assert "aditya2kx" in errors[0]


def test_operator_then_agent_passes():
    comments = [
        _make_comment(1, "aditya2kx"),
        _make_comment(2, "jarvis-agent-bot328"),
    ]
    errors = _run(comments)
    assert errors == []


def test_agent_then_operator_fails():
    """Agent reply before operator comment doesn't count."""
    comments = [
        _make_comment(1, "jarvis-agent-bot328"),
        _make_comment(2, "aditya2kx"),
    ]
    errors = _run(comments)
    assert len(errors) == 1


def test_ignored_bot_logins_skipped():
    """github-actions[bot] and other bots never need replies."""
    comments = [
        _make_comment(1, "github-actions[bot]"),
        _make_comment(2, "claude[bot]"),
        _make_comment(3, "dependabot[bot]"),
    ]
    errors = _run(comments)
    assert errors == []


def test_empty_list_passes():
    errors = _run([])
    assert errors == []


def test_multiple_operator_comments_all_need_reply():
    comments = [
        _make_comment(1, "aditya2kx"),
        _make_comment(2, "aditya2kx"),
    ]
    errors = _run(comments)
    assert len(errors) == 2


def test_operator_agent_operator_fails_last():
    """Second operator comment after agent reply must also be addressed."""
    comments = [
        _make_comment(1, "aditya2kx"),
        _make_comment(2, "jarvis-agent-bot328"),
        _make_comment(3, "aditya2kx"),
    ]
    errors = _run(comments)
    assert len(errors) == 1
    assert "3" in errors[0]
