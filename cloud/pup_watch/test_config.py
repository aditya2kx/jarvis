"""Camera source of truth, Firestore overlay, recipient parsing."""

import json

import pytest

from cloud.pup_watch import config


def test_shipped_cameras_json_includes_the_sm_yard():
    cams = config.load_cameras()
    assert [c.name for c in cams] == ["sm-yard"]
    cam = cams[0]
    assert cam.alias == "5ee276849d4bf"
    assert cam.label == "S/M YARD"
    # Tiling is what carries recall at the far fence; regions must be present.
    assert len(cam.yard_regions) == 2
    assert all(len(r) == 4 for r in cam.yard_regions)


def test_multiple_cameras_are_supported(tmp_path):
    """If the daycare exposes another yard we add config, not code."""
    path = tmp_path / "cameras.json"
    path.write_text(json.dumps({"cameras": [
        {"name": "sm-yard", "alias": "aaa", "yard_regions": [[0, 0, 10, 10]]},
        {"name": "big-yard", "alias": "bbb", "label": "BIG YARD"},
    ]}))
    cams = config.load_cameras(path)
    assert [c.name for c in cams] == ["sm-yard", "big-yard"]
    assert cams[1].yard_regions == ()


def test_empty_camera_list_is_rejected(tmp_path):
    path = tmp_path / "cameras.json"
    path.write_text(json.dumps({"cameras": []}))
    with pytest.raises(ValueError):
        config.load_cameras(path)


def test_overlay_absent_returns_defaults():
    assert config.settings_with_overlay(None) == config.Settings()
    assert config.settings_with_overlay({}) == config.Settings()


def test_overlay_applies_and_coerces_types():
    s = config.settings_with_overlay({
        "absence_minutes": "35",
        "dog_score_min": 0.4,
        "min_hits_per_poll": 3.0,
    })
    assert s.absence_minutes == 35
    assert s.dog_score_min == pytest.approx(0.4)
    assert s.min_hits_per_poll == 3


def test_overlay_ignores_unknown_and_unparseable_keys():
    s = config.settings_with_overlay({
        "absence_minutes": "not-a-number",
        "totally_unknown": 5,
        "dog_score_min": None,
    })
    assert s.absence_minutes == config.Settings().absence_minutes
    assert s.dog_score_min == config.Settings().dog_score_min
    assert not hasattr(s, "totally_unknown")


def test_overlay_can_disable_gemini_confirmation():
    assert config.settings_with_overlay({"require_gemini_confirm": False}).require_gemini_confirm is False
    # A non-bool must not flip the flag.
    assert config.settings_with_overlay({"require_gemini_confirm": "no"}).require_gemini_confirm is True


def test_recipients_parse_both_addresses(monkeypatch):
    monkeypatch.setenv("PUPWATCH_NOTIFY_TO", "a@example.com, b@example.com")
    assert config.notify_recipients() == ["a@example.com", "b@example.com"]


def test_recipients_tolerate_semicolons_and_blanks(monkeypatch):
    monkeypatch.setenv("PUPWATCH_NOTIFY_TO", " a@example.com ;; ,b@example.com,")
    assert config.notify_recipients() == ["a@example.com", "b@example.com"]


def test_recipients_absent_is_empty(monkeypatch):
    monkeypatch.delenv("PUPWATCH_NOTIFY_TO", raising=False)
    assert config.notify_recipients() == []


def test_no_email_address_literal_in_shipped_source():
    """Personal addresses belong in env, never in git.

    tesla-aladdin-garage hardcodes a DEFAULT_TO address; pup-watch must not
    repeat that, since this list includes a second person's address.
    """
    import re
    from pathlib import Path

    email = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
    pkg = Path(config.__file__).parent
    offenders = []
    for path in sorted(pkg.rglob("*")):
        if path.suffix not in {".py", ".json", ".txt", ".md"} or path.name.startswith("test_"):
            continue
        for match in email.findall(path.read_text()):
            # Placeholders in docs are fine; real mailboxes are not.
            if match.endswith((".example", "example.com", ".invalid")):
                continue
            offenders.append(f"{path.name}:{match}")
    assert not offenders, f"hardcoded addresses: {offenders}"
