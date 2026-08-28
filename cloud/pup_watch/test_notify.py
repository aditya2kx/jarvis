"""Email shape: both recipients on one message, frame attached, no send without config."""

from cloud.pup_watch import notify

FIELDS = {
    "camera": "sm-yard",
    "camera_label": "S/M YARD",
    "seen_ts": 1_800_000_000.0,
    "dogs": 1,
    "persons": 2,
    "hits": 3,
    "frames": 4,
    "dog_box_px": 72,
    "cream_fraction": 0.81,
    "identity_confidence": 0.92,
    "identity_notes": "cream coat, golden build",
}


def test_build_message_puts_both_recipients_on_one_to_header():
    msg = notify.build_message(
        ["a@example.com", "b@example.com"], "a@example.com", "subj", "text"
    )
    assert msg["to"] == "a@example.com, b@example.com"


def test_build_message_attaches_the_frame_as_jpeg():
    msg = notify.build_message(["a@example.com"], "a@example.com", "s", "t", image=b"\xff\xd8fakejpeg")
    subtypes = [p.get_content_subtype() for p in msg.walk()]
    assert "jpeg" in subtypes
    filenames = [p.get_filename() for p in msg.walk() if p.get_filename()]
    assert filenames == ["sighting.jpg"]


def test_build_message_without_image_has_no_attachment():
    msg = notify.build_message(["a@example.com"], "a@example.com", "s", "t")
    assert [p.get_filename() for p in msg.walk() if p.get_filename()] == []


def test_subject_names_the_yard_and_match_confidence():
    assert notify.subject(FIELDS) == "Pup is out in the S/M YARD (match 92%)"


def test_subject_without_identity_confidence_omits_the_match():
    fields = {k: v for k, v in FIELDS.items() if k != "identity_confidence"}
    assert notify.subject(fields) == "Pup is out in the S/M YARD"


def test_body_reports_the_evidence_that_fired():
    text = notify.body(FIELDS)
    assert "Dogs in yard: 1" in text
    assert "People in yard: 2" in text
    assert "Frames agreeing this poll: 3 of 4" in text
    assert "72 px" in text
    assert "81%" in text
    assert "92% confident" in text
    assert "cream coat, golden build" in text


def test_body_renders_local_time_not_utc():
    # 1_800_000_000 is 2027-01-15 08:00:00 UTC -> 02:00:00 CST
    assert "2:00:00 AM CST" in notify.body(FIELDS)


def test_body_surfaces_a_skipped_identity_check():
    fields = {k: v for k, v in FIELDS.items() if k != "identity_confidence"}
    fields["identity_skipped"] = "no_token"
    text = notify.body(fields)
    assert "Identity check skipped: no_token" in text


def test_body_tolerates_missing_optional_fields():
    text = notify.body({"camera": "sm-yard"})
    assert "Spotted just now" in text


def test_send_skips_without_recipients(monkeypatch):
    monkeypatch.delenv("PUPWATCH_NOTIFY_TO", raising=False)
    monkeypatch.setenv("GMAIL_CLIENT_ID", "id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "refresh")
    assert notify.send_sighting(FIELDS) is False


def test_send_skips_without_gmail_credentials(monkeypatch):
    monkeypatch.setenv("PUPWATCH_NOTIFY_TO", "a@example.com")
    for var in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    assert notify.send_sighting(FIELDS) is False


def test_send_sends_once_to_both_and_returns_true(monkeypatch):
    monkeypatch.setenv("PUPWATCH_NOTIFY_TO", "a@example.com,b@example.com")
    monkeypatch.setenv("GMAIL_CLIENT_ID", "id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "refresh")
    monkeypatch.setattr(notify, "_access_token", lambda *a: "token")
    sent = []
    monkeypatch.setattr(notify, "_gmail_send", lambda access, msg: sent.append(msg))
    assert notify.send_sighting(FIELDS, image=b"\xff\xd8jpeg") is True
    assert len(sent) == 1
    assert sent[0]["to"] == "a@example.com, b@example.com"


def test_send_swallows_transport_errors(monkeypatch):
    """A failed email must not take the poll down with it."""
    monkeypatch.setenv("PUPWATCH_NOTIFY_TO", "a@example.com")
    monkeypatch.setenv("GMAIL_CLIENT_ID", "id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "refresh")

    def boom(*a, **k):
        raise RuntimeError("gmail exploded")

    monkeypatch.setattr(notify, "_access_token", boom)
    assert notify.send_sighting(FIELDS) is False
