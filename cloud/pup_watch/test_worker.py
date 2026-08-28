"""Poll orchestration: session gating, stage ordering, identity veto, email fan-out."""

import pytest

from cloud.pup_watch import identify, notify, persist, stream, vision, worker
from cloud.pup_watch.config import COCO_DOG, COCO_PERSON, Camera, Settings

CAM = Camera(name="sm-yard", alias="alias123", label="S/M YARD", yard_regions=())
S = Settings()
NOW = 1_800_000_000.0


# --------------------------- session gating ---------------------------

def test_session_inactive_when_absent():
    assert worker.session_active({}, now=NOW, settings=S) == (False, "no_active_session")


def test_session_inactive_when_flag_false():
    active, why = worker.session_active({"active": False}, now=NOW, settings=S)
    assert (active, why) == (False, "no_active_session")


def test_session_active_within_window():
    session = {"active": True, "started_ts": NOW - 60, "stop_after_ts": NOW + 3600}
    assert worker.session_active(session, now=NOW, settings=S) == (True, "active")


def test_session_expires_at_stop_after():
    session = {"active": True, "started_ts": NOW - 3600, "stop_after_ts": NOW - 1}
    active, why = worker.session_active(session, now=NOW, settings=S)
    assert (active, why) == (False, "session_expired_stop_after")


def test_session_expires_after_max_hours_even_without_stop_after():
    """A forgotten session must stop polling rather than run forever."""
    session = {"active": True, "started_ts": NOW - (S.session_max_hours * 3600 + 10)}
    active, why = worker.session_active(session, now=NOW, settings=S)
    assert (active, why) == (False, "session_expired_max_hours")


def test_session_survives_corrupt_timestamps():
    session = {"active": True, "started_ts": "bogus", "stop_after_ts": None}
    assert worker.session_active(session, now=NOW, settings=S) == (True, "active")


# --------------------------- camera evaluation ---------------------------

def _stub_stream(monkeypatch, *, usable=True, frames=4, raise_on=None):
    info = stream.StreamInfo(
        alias=CAM.alias,
        playlist_url="https://host/streams/x/stream.m3u8" if usable else "",
        available=usable,
        health=97,
        motion_diff=0.1,
        brightness=0.5,
    )

    def resolve(alias, timeout=15.0):
        if raise_on == "resolve":
            raise stream.StreamUnavailable("state_fetch_failed")
        return info

    def grab(url, count=4, interval_s=2.0, timeout_s=45.0):
        if raise_on == "grab":
            raise stream.StreamUnavailable("ffmpeg_no_frames")
        return [f"frame{i}".encode() for i in range(frames)]

    monkeypatch.setattr(stream, "resolve_stream", resolve)
    monkeypatch.setattr(stream, "grab_frames", grab)


def _stub_analyse(monkeypatch, verdicts):
    """Feed a scripted verdict per frame."""
    seq = list(verdicts)

    def analyse(frame, *, settings, regions=()):
        return seq.pop(0) if seq else _verdict(False)

    monkeypatch.setattr(vision, "analyse_frame", analyse)


def _verdict(lone, *, dogs=1, persons=0, cream=0.8, score=0.7, reason=""):
    dog_dets = tuple(
        vision.Detection(COCO_DOG, score - 0.01 * i, (300, 300 + 80 * i, 340, 380 + 80 * i))
        for i in range(dogs)
    )
    person_dets = tuple(
        vision.Detection(COCO_PERSON, 0.8, (500, 250, 540, 400)) for _ in range(persons)
    )
    return vision.FrameVerdict(
        dogs=dog_dets,
        persons=person_dets,
        cream=vision.CreamStats(cream, 200.0, 30.0),
        lone_cream_dog=lone,
        reason=reason or ("lone_cream_dog" if lone else "no_dog"),
    )


@pytest.fixture(autouse=True)
def _no_gemini(monkeypatch):
    """Default: identity check is unavailable, so it must not suppress sightings."""
    monkeypatch.setattr(
        identify, "confirm_pup",
        lambda crop, **kw: identify.Identification(False, 0.0, 0, skipped="no_token"),
    )
    monkeypatch.setattr(vision, "crop_detection", lambda frame, box, pad=0.18: b"crop")
    monkeypatch.setattr(vision, "annotate", lambda frame, dets: b"annotated")


def test_zero_hits_reports_both_the_count_and_the_frame_reason(monkeypatch):
    """The breadcrumb must carry enough state to diagnose from logs alone."""
    _stub_stream(monkeypatch, frames=2)
    _stub_analyse(monkeypatch, [_verdict(False, reason="multiple_dogs n=3")] * 2)
    result = worker.evaluate_camera(CAM, S)
    assert "insufficient_hits 0/2" in result.reason
    assert "multiple_dogs n=3" in result.reason


def test_offline_camera_is_skipped_without_grabbing_frames(monkeypatch):
    _stub_stream(monkeypatch, usable=False)
    result = worker.evaluate_camera(CAM, S)
    assert result.seen is False
    assert result.reason == "camera_offline"
    assert result.frames == 0


def test_stream_resolve_failure_is_contained(monkeypatch):
    _stub_stream(monkeypatch, raise_on="resolve")
    result = worker.evaluate_camera(CAM, S)
    assert result.seen is False
    assert "stream_unavailable" in result.reason


def test_frame_grab_failure_is_contained(monkeypatch):
    _stub_stream(monkeypatch, raise_on="grab")
    result = worker.evaluate_camera(CAM, S)
    assert result.seen is False
    assert "frame_grab_failed" in result.reason


def test_two_agreeing_frames_make_a_sighting(monkeypatch):
    _stub_stream(monkeypatch, frames=4)
    _stub_analyse(monkeypatch, [_verdict(True), _verdict(False), _verdict(True), _verdict(False)])
    result = worker.evaluate_camera(CAM, S)
    assert result.seen is True
    assert result.hits == 2
    assert result.frames == 4
    assert result.dog_box_px > 0


def test_single_agreeing_frame_is_not_enough(monkeypatch):
    """One frame is a fluke; the threshold exists to reject it."""
    _stub_stream(monkeypatch, frames=4)
    _stub_analyse(monkeypatch, [_verdict(True)] + [_verdict(False)] * 3)
    result = worker.evaluate_camera(CAM, S)
    assert result.seen is False
    assert result.hits == 1
    assert "insufficient_hits 1/2" in result.reason


def test_a_bad_frame_does_not_abort_the_poll(monkeypatch):
    _stub_stream(monkeypatch, frames=3)
    calls = {"n": 0}

    def analyse(frame, *, settings, regions=()):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("corrupt jpeg")
        return _verdict(True)

    monkeypatch.setattr(vision, "analyse_frame", analyse)
    result = worker.evaluate_camera(CAM, S)
    assert result.seen is True
    assert result.hits == 2


def test_person_count_is_reported_but_never_blocks(monkeypatch):
    _stub_stream(monkeypatch, frames=2)
    _stub_analyse(monkeypatch, [_verdict(True, persons=2), _verdict(True, persons=1)])
    result = worker.evaluate_camera(CAM, S)
    assert result.seen is True
    assert result.persons == 2


# --------------------------- identity veto ---------------------------

def test_confident_wrong_dog_vetoes_the_sighting(monkeypatch):
    _stub_stream(monkeypatch, frames=2)
    _stub_analyse(monkeypatch, [_verdict(True), _verdict(True)])
    monkeypatch.setattr(
        identify, "confirm_pup",
        lambda crop, **kw: identify.Identification(False, 0.95, 1, coat="black"),
    )
    result = worker.evaluate_camera(CAM, S)
    assert result.seen is False
    assert "identity_rejected" in result.reason


def test_confirmed_pup_passes_through(monkeypatch):
    _stub_stream(monkeypatch, frames=2)
    _stub_analyse(monkeypatch, [_verdict(True), _verdict(True)])
    monkeypatch.setattr(
        identify, "confirm_pup",
        lambda crop, **kw: identify.Identification(True, 0.93, 1, coat="cream"),
    )
    result = worker.evaluate_camera(CAM, S)
    assert result.seen is True
    assert result.identity.confidence == pytest.approx(0.93)


def test_inconclusive_identity_check_does_not_suppress(monkeypatch):
    """No token / no references must fail open, not silently swallow sightings."""
    _stub_stream(monkeypatch, frames=2)
    _stub_analyse(monkeypatch, [_verdict(True), _verdict(True)])
    result = worker.evaluate_camera(CAM, S)  # autouse fixture returns skipped=no_token
    assert result.seen is True
    assert result.identity.skipped == "no_token"


def test_identity_check_not_called_when_disabled(monkeypatch):
    _stub_stream(monkeypatch, frames=2)
    _stub_analyse(monkeypatch, [_verdict(True), _verdict(True)])
    called = {"n": 0}

    def spy(crop, **kw):
        called["n"] += 1
        return identify.Identification(True, 1.0, 1)

    monkeypatch.setattr(identify, "confirm_pup", spy)
    result = worker.evaluate_camera(CAM, Settings(require_gemini_confirm=False))
    assert result.seen is True
    assert called["n"] == 0


def test_identity_check_not_called_when_local_stages_reject(monkeypatch):
    """The paid stage must only run on candidates — that is the cost model."""
    _stub_stream(monkeypatch, frames=2)
    _stub_analyse(monkeypatch, [_verdict(False), _verdict(False)])
    called = {"n": 0}
    monkeypatch.setattr(
        identify, "confirm_pup",
        lambda crop, **kw: (called.__setitem__("n", called["n"] + 1), identify.Identification(True, 1.0, 1))[1],
    )
    worker.evaluate_camera(CAM, S)
    assert called["n"] == 0


# --------------------------- tick ---------------------------

@pytest.fixture
def store(monkeypatch):
    docs = {"config": {}, "session": {}, "state": {}}
    monkeypatch.setattr(persist, "load_config", lambda: dict(docs["config"]))
    monkeypatch.setattr(persist, "load_session", lambda: dict(docs["session"]))
    monkeypatch.setattr(persist, "load_state", lambda: dict(docs["state"]))
    monkeypatch.setattr(persist, "save_session", lambda s: docs["session"].update(s))
    monkeypatch.setattr(persist, "save_state", lambda s: docs.__setitem__("state", dict(s)))
    return docs


def test_tick_without_session_does_no_work(store, monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(stream, "resolve_stream", lambda *a, **k: called.__setitem__("n", 1))
    out = worker.tick(now=NOW)
    assert out == {"polled": False, "reason": "no_active_session"}
    assert called["n"] == 0


def test_tick_auto_stops_an_expired_session(store):
    store["session"].update({"active": True, "started_ts": NOW - 99999, "stop_after_ts": NOW - 1})
    out = worker.tick(now=NOW)
    assert out["polled"] is False
    assert out["reason"] == "session_expired_stop_after"
    assert store["session"]["active"] is False


def test_tick_sends_one_email_on_first_sighting(store, monkeypatch):
    store["session"].update({"active": True, "started_ts": NOW, "stop_after_ts": NOW + 3600})
    _stub_stream(monkeypatch, frames=2)
    _stub_analyse(monkeypatch, [_verdict(True), _verdict(True)])
    sent = []
    monkeypatch.setattr(notify, "send_sighting", lambda fields, image=None: sent.append((fields, image)) or True)
    out = worker.tick(now=NOW)
    assert out["polled"] is True
    assert out["notified"] is True
    assert len(sent) == 1
    fields, image = sent[0]
    assert fields["camera_label"] == "S/M YARD"
    assert fields["hits"] == 2
    assert image == b"annotated"


def test_tick_does_not_email_twice_for_the_same_visit(store, monkeypatch):
    store["session"].update({"active": True, "started_ts": NOW, "stop_after_ts": NOW + 36000})
    _stub_stream(monkeypatch, frames=2)
    sent = []
    monkeypatch.setattr(notify, "send_sighting", lambda fields, image=None: sent.append(fields) or True)
    for i in range(4):
        _stub_analyse(monkeypatch, [_verdict(True), _verdict(True)])
        worker.tick(now=NOW + i * 60)
    assert len(sent) == 1


def test_tick_reports_not_notified_when_nothing_seen(store, monkeypatch):
    store["session"].update({"active": True, "started_ts": NOW, "stop_after_ts": NOW + 3600})
    _stub_stream(monkeypatch, frames=2)
    _stub_analyse(monkeypatch, [_verdict(False), _verdict(False)])
    monkeypatch.setattr(notify, "send_sighting", lambda *a, **k: pytest.fail("must not email"))
    out = worker.tick(now=NOW)
    assert out["notified"] is False
    assert out["cameras"][0]["seen"] is False


def test_tick_restricts_to_session_cameras(store, monkeypatch):
    store["session"].update({
        "active": True, "started_ts": NOW, "stop_after_ts": NOW + 3600,
        "cameras": ["sm-yard"],
    })
    seen_aliases = []
    monkeypatch.setattr(stream, "resolve_stream", lambda alias, timeout=15.0: seen_aliases.append(alias) or stream.StreamInfo(alias, "", False, 0, 0.0, 0.0))
    worker.tick(now=NOW)
    assert seen_aliases == ["5ee276849d4bf"]
