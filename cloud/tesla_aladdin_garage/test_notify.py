"""Notify is a no-op without Gmail env; does not raise."""

from cloud.tesla_aladdin_garage.notify import (
    email_body,
    email_subject,
    notify_runtime_allowed,
    send_garage_email,
    should_email,
)


def test_notify_skips_when_unconfigured(monkeypatch):
    for key in (
        "GARAGE_GMAIL_CLIENT_ID",
        "GARAGE_GMAIL_CLIENT_SECRET",
        "GARAGE_GMAIL_REFRESH_TOKEN",
    ):
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
    assert "POST /config" in body


def test_should_email_only_real_opened():
    live = {"enter_m": 300, "distance_m": 187, "simulated": False}
    assert should_email("opened", live) is True
    assert should_email("opened", {**live, "simulated": True}) is False
    assert should_email("skip_already_open", live) is False
    assert should_email("open_error", live) is False


def test_send_skips_simulated_even_when_gmail_configured(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "tesla-aladdin-garage")
    monkeypatch.setenv("GARAGE_GMAIL_CLIENT_ID", "id")
    monkeypatch.setenv("GARAGE_GMAIL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GARAGE_GMAIL_REFRESH_TOKEN", "refresh")

    def boom(*_args, **_kwargs):
        raise AssertionError("gmail must not send")

    monkeypatch.setattr("cloud.tesla_aladdin_garage.notify._access_token", boom)
    monkeypatch.setattr("cloud.tesla_aladdin_garage.notify._gmail_send", boom)
    assert send_garage_email(
        "opened",
        {"enter_m": 400, "distance_m": 0, "simulated": True},
    ) is False
    assert send_garage_email("open_error", {"enter_m": 300, "distance_m": 267}) is False


def test_pup_watch_credentials_do_not_drive_the_garage(monkeypatch):
    """Issue #316: pup-watch mails from the bare GMAIL_* names; they are not ours."""
    monkeypatch.setenv("K_SERVICE", "tesla-aladdin-garage")
    monkeypatch.setenv("GMAIL_CLIENT_ID", "pupwatch-id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "pupwatch-secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "pupwatch-refresh")

    def boom(*_args, **_kwargs):
        raise AssertionError("garage must not borrow pup-watch credentials")

    monkeypatch.setattr("cloud.tesla_aladdin_garage.notify._access_token", boom)
    monkeypatch.setattr("cloud.tesla_aladdin_garage.notify._gmail_send", boom)
    monkeypatch.setattr(
        "cloud.tesla_aladdin_garage.notify.month_tesla_cost", lambda: boom()
    )
    assert send_garage_email(
        "opened", {"enter_m": 300, "distance_m": 187, "simulated": False}
    ) is False


def test_runtime_gate_requires_cloud_run(monkeypatch):
    """Issue #316: the unit suite emailed the operator from a laptop shell."""
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("GARAGE_NOTIFY_FORCE", raising=False)
    assert notify_runtime_allowed() is False
    monkeypatch.setenv("K_SERVICE", "tesla-aladdin-garage")
    assert notify_runtime_allowed() is True
    monkeypatch.delenv("K_SERVICE")
    monkeypatch.setenv("GARAGE_NOTIFY_FORCE", "1")
    assert notify_runtime_allowed() is True


def test_real_open_never_sends_off_cloud_run(monkeypatch):
    """A live `opened` with credentials present is still silent on a laptop."""
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setenv("GARAGE_GMAIL_CLIENT_ID", "id")
    monkeypatch.setenv("GARAGE_GMAIL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GARAGE_GMAIL_REFRESH_TOKEN", "refresh")

    def boom(*_args, **_kwargs):
        raise AssertionError("gmail must not send off Cloud Run")

    monkeypatch.setattr("cloud.tesla_aladdin_garage.notify._access_token", boom)
    monkeypatch.setattr("cloud.tesla_aladdin_garage.notify._gmail_send", boom)
    monkeypatch.setattr(
        "cloud.tesla_aladdin_garage.notify.month_tesla_cost", lambda: boom()
    )
    assert send_garage_email(
        "opened", {"enter_m": 400, "distance_m": 0, "simulated": False}
    ) is False
