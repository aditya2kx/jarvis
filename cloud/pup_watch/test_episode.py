"""Episode rules: one email per visit, never one per poll."""

from cloud.pup_watch.config import Settings
from cloud.pup_watch.episode import decide

S = Settings(absence_minutes=20, notify_cooldown_minutes=10)
MIN = 60.0


def test_first_sighting_notifies_and_opens_episode():
    d = decide(None, seen=True, now=1000.0, settings=S, camera="sm-yard")
    assert d.notify is True
    assert d.reason == "episode_started"
    assert d.state["episode_active"] is True
    assert d.state["last_notified_ts"] == 1000.0
    assert d.state["last_camera"] == "sm-yard"


def test_repeat_sighting_in_same_episode_does_not_notify():
    first = decide(None, seen=True, now=1000.0, settings=S)
    second = decide(first.state, seen=True, now=1000.0 + 60, settings=S)
    third = decide(second.state, seen=True, now=1000.0 + 600, settings=S)
    assert (second.notify, second.reason) == (False, "already_in_episode")
    assert (third.notify, third.reason) == (False, "already_in_episode")


def test_brief_disappearance_does_not_close_episode():
    """He steps behind the playhouse for a few polls; still the same visit."""
    state = decide(None, seen=True, now=0.0, settings=S).state
    for minute in (1, 2, 3):
        d = decide(state, seen=False, now=minute * MIN, settings=S)
        state = d.state
        assert d.reason == "not_seen"
        assert state["episode_active"] is True
    back = decide(state, seen=True, now=4 * MIN, settings=S)
    assert back.notify is False
    assert back.reason == "already_in_episode"


def test_episode_closes_after_absence_window():
    state = decide(None, seen=True, now=0.0, settings=S).state
    closed = decide(state, seen=False, now=21 * MIN, settings=S)
    assert closed.episode_ended is True
    assert closed.reason == "episode_closed"
    assert closed.state["episode_active"] is False


def test_second_visit_notifies_again_after_absence_and_cooldown():
    state = decide(None, seen=True, now=0.0, settings=S).state
    state = decide(state, seen=False, now=25 * MIN, settings=S).state
    again = decide(state, seen=True, now=26 * MIN, settings=S)
    assert again.notify is True
    assert again.reason == "episode_started"


def test_cooldown_suppresses_a_second_email_even_across_episodes():
    """Flapping detector: episode closes and reopens fast. Only one email."""
    fast = Settings(absence_minutes=1, notify_cooldown_minutes=30)
    state = decide(None, seen=True, now=0.0, settings=fast).state
    state = decide(state, seen=False, now=2 * MIN, settings=fast).state
    reopen = decide(state, seen=True, now=3 * MIN, settings=fast)
    assert reopen.notify is False
    assert reopen.reason == "cooldown_active"
    assert reopen.state["episode_active"] is True


def test_not_seen_from_cold_state_is_a_noop():
    d = decide({}, seen=False, now=500.0, settings=S)
    assert (d.notify, d.reason) == (False, "not_seen")
    assert d.state["last_poll_ts"] == 500.0


def test_last_poll_ts_always_advances():
    state = {}
    for i in range(1, 4):
        state = decide(state, seen=False, now=float(i), settings=S).state
        assert state["last_poll_ts"] == float(i)


def test_corrupt_timestamps_do_not_crash():
    bad = {"episode_active": True, "last_seen_ts": "not-a-number", "last_notified_ts": None}
    d = decide(bad, seen=True, now=100.0, settings=S)
    assert d.reason == "already_in_episode"
