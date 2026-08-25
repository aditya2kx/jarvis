"""Notify is a no-op without Gmail env; does not raise."""

from cloud.tesla_aladdin_garage.notify import email_body, email_subject, send_garage_email


def test_notify_skips_when_unconfigured(monkeypatch):
    for key in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    assert send_garage_email("opened", {"enter_m": 400, "distance_m": 187}) is False


def test_subject_and_body_include_tesla_distance():
    fields = {
        "enter_m": 800,
        "distance_m": 187.4,
        "door": "Big Peach",
        "tesla_cost_lines": ["Tesla Fleet (2026-08): $1.20 / $10.00 credit (within cap)"],
        "tesla_cost_subject": "Tesla Fleet $1.20/$10",
    }
    subj = email_subject("opened", fields)
    assert "187 m" in subj
    assert "enter 800 m" in subj
    assert "Tesla Fleet $1.20/$10" in subj
    body = email_body("skip_already_open", fields)
    assert body.startswith("Tesla distance from home when this fired: 187 m")
    assert "800 m" in body
    assert "$1.20 / $10.00" in body
