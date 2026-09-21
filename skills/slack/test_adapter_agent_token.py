"""The agent identity must survive the whole ask-and-wait round trip.

REGRESSION (live 2026-09-21, ADP payroll draft): ``request_otp(agent="bhaga")``
DM'd the operator as BHAGA and then raised "Keychain lookup failed for agent
'default'" while trying to read the answer. Every *write* threaded the agent
through; ``read_replies`` had no ``agent`` parameter at all, so it silently fell
back to the default token, whose Keychain entry (service ``jarvis``) does not
exist on this machine. The prompt went out, the reply was unreadable, and the
ADP login stalled on a code the operator had already sent.
"""

from __future__ import annotations

import inspect

import skills.slack.adapter as adapter


def _capture(monkeypatch):
    """Wire the adapter so we can see which agent each call used."""
    seen: dict[str, list] = {"read": [], "send": [], "dm": []}
    monkeypatch.setattr(
        adapter, "open_dm",
        lambda user_id, agent=None: seen["dm"].append(agent) or "D1",
    )
    monkeypatch.setattr(
        adapter, "send_message",
        lambda ch, msg, agent=None: seen["send"].append(agent) or {"ts": "1"},
    )
    monkeypatch.setattr(
        adapter, "read_replies",
        lambda ch, oldest=None, limit=5, agent=None: (
            seen["read"].append(agent)
            or [{"ts": "2", "user": "U1", "text": "123456"}]
        ),
    )
    monkeypatch.setattr(adapter.time, "sleep", lambda s: None)
    # Force the polling path: Socket Mode would not exercise read_replies.
    monkeypatch.setitem(
        __import__("sys").modules, "skills.slack.listener", None
    )
    return seen


def test_read_replies_accepts_an_agent():
    """Without the parameter there is no way to thread identity at all."""
    assert "agent" in inspect.signature(adapter.read_replies).parameters


def test_read_replies_passes_the_agent_to_the_api_call():
    src = inspect.getsource(adapter.read_replies)
    assert src.count("agent=agent") == 2, "both history and replies paths"


def test_request_otp_reads_as_the_same_agent_it_wrote_as(monkeypatch):
    seen = _capture(monkeypatch)
    code = adapter.request_otp(
        "U1", "ADP", timeout_seconds=30, poll_interval=0, agent="bhaga"
    )
    assert code == "123456"
    assert seen["read"], "polling path must have been exercised"
    assert set(seen["read"]) == {"bhaga"}
    # The DM was opened as BHAGA, so only BHAGA's app can read that channel.
    assert set(seen["read"]) == set(seen["dm"])


def test_request_reply_reads_as_the_same_agent_it_wrote_as(monkeypatch):
    seen = _capture(monkeypatch)
    adapter.request_reply(
        "U1", "paste the link", timeout_seconds=30, poll_interval=0, agent="bhaga"
    )
    assert set(seen["read"]) == {"bhaga"}


def test_no_read_replies_call_in_the_adapter_omits_the_agent(monkeypatch):
    """A single unthreaded call is enough to resurrect the bug."""
    src = inspect.getsource(adapter)
    calls = [
        seg for seg in src.split("read_replies(")[1:]
        if not seg.lstrip().startswith("channel, thread_ts")  # the def itself
    ]
    for seg in calls:
        head = seg.split(")")[0]
        assert "agent=" in head, f"read_replies call without agent: {head!r}"
