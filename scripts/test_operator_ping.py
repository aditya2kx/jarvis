"""operator_ping: Slack DM to the operator for a tap / code / click."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import operator_ping as OP  # noqa: E402


def test_notify_only_sends_one_dm(monkeypatch, capsys):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    with patch.object(OP.adapter, "open_dm", return_value="D1") as dm, \
         patch.object(OP.adapter, "send_message") as send:
        assert OP.main(["tap Yes"]) == 0
    dm.assert_called_once_with(OP.OPERATOR, agent="bhaga")
    assert send.call_args.args[0] == "D1" and "tap Yes" in send.call_args.args[1]
    assert "sent" in capsys.readouterr().out


def test_wait_types_reply_without_printing_it(monkeypatch, capsys):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    with patch.object(OP.adapter, "request_reply", return_value="123456"), \
         patch.object(OP, "type_text") as typed:
        assert OP.main(["code?", "--wait", "60", "--type", "--enter"]) == 0
    typed.assert_called_once_with("123456", True)
    assert "123456" not in capsys.readouterr().out


def test_no_reply_and_no_token_exit_codes(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    with patch.object(OP.adapter, "request_reply", return_value=None):
        assert OP.main(["code?", "--wait", "1"]) == 2
    monkeypatch.delenv("SLACK_BOT_TOKEN")
    with patch.object(OP.G, "cursor_oidc_token", side_effect=OSError("no socket")):
        assert OP.main(["hi"]) == 1
