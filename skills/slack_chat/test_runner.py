"""Slack staff-workspace client: ClickUp-markdown → mrkdwn and the user list shape."""

from unittest import mock

from skills.slack_chat import runner


def test_to_mrkdwn_converts_bold_bullets_and_everyone():
    md = "@everyone hi\n*   **Dolce Johnson** leading with $40.\n*   **A** and **B** at $20 each."
    assert runner.to_mrkdwn(md) == (
        "<!channel> hi\n• *Dolce Johnson* leading with $40.\n• *A* and *B* at $20 each."
    )


def test_list_members_skips_bots_and_deleted_and_pages():
    pages = [
        {"ok": True, "members": [
            {"id": "U1", "profile": {"real_name": "Dolce Johnson", "email": "d@x.co"}},
            {"id": "B1", "is_bot": True, "profile": {"real_name": "Palmetto"}},
            {"id": "U9", "deleted": True, "profile": {"real_name": "Gone"}},
        ], "response_metadata": {"next_cursor": "c2"}},
        {"ok": True, "members": [{"id": "U2", "real_name": "Liz Moreno", "profile": {}}],
         "response_metadata": {"next_cursor": ""}},
    ]
    with mock.patch.object(runner, "_call", side_effect=pages):
        assert runner.list_members(token="x") == [
            {"id": "U1", "username": "Dolce Johnson", "email": "d@x.co"},
            {"id": "U2", "username": "Liz Moreno", "email": ""},
        ]


def test_post_message_sends_mrkdwn_and_returns_ts():
    with mock.patch.object(runner, "_call", return_value={"ok": True, "ts": "1.2", "channel": "C1"}) as call:
        assert runner.post_message("C1", "**hi**", token="x") == {"id": "1.2", "channel": "C1"}
    assert call.call_args.kwargs["payload"]["text"] == "*hi*"
