"""Identity confirmation: request shape, thresholds, and failing open."""

import base64
import json

from cloud.pup_watch import identify

CROP = b"\xff\xd8candidate"
REFS = (b"\xff\xd8ref1", b"\xff\xd8ref2")
KW = {"model": "gemini-2.5-flash-lite", "confidence_min": 0.55}


def _reply(payload: dict) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]}


def _stub(monkeypatch, payload, *, capture=None):
    monkeypatch.setenv("PUPWATCH_GEMINI_TOKEN", "key123")
    monkeypatch.setattr(identify, "reference_images", lambda: REFS)

    def post(url, body, timeout):
        if capture is not None:
            capture["url"] = url
            capture["body"] = body
        return _reply(payload)

    monkeypatch.setattr(identify, "_post", post)


def test_missing_token_is_skipped_not_a_rejection(monkeypatch):
    monkeypatch.delenv("PUPWATCH_GEMINI_TOKEN", raising=False)
    monkeypatch.delenv("GEMINI_TOKEN", raising=False)
    ident = identify.confirm_pup(CROP, **KW)
    assert ident.skipped == "no_token"
    assert ident.conclusive is False


def test_missing_references_is_skipped(monkeypatch):
    monkeypatch.setenv("PUPWATCH_GEMINI_TOKEN", "key123")
    monkeypatch.setattr(identify, "reference_images", lambda: ())
    ident = identify.confirm_pup(CROP, **KW)
    assert ident.skipped == "no_reference_images"
    assert ident.conclusive is False


def test_confident_match_is_accepted(monkeypatch):
    _stub(monkeypatch, {"is_pup": True, "confidence": 0.91, "dogs_visible": 1, "coat": "cream"})
    ident = identify.confirm_pup(CROP, **KW)
    assert ident.is_pup is True
    assert ident.conclusive is True
    assert ident.coat == "cream"


def test_low_confidence_match_is_not_accepted(monkeypatch):
    _stub(monkeypatch, {"is_pup": True, "confidence": 0.40, "dogs_visible": 1})
    assert identify.confirm_pup(CROP, **KW).is_pup is False


def test_more_than_one_dog_visible_is_not_our_pup(monkeypatch):
    """He is only ever out alone, so two dogs in the crop rules him out."""
    _stub(monkeypatch, {"is_pup": True, "confidence": 0.99, "dogs_visible": 2})
    assert identify.confirm_pup(CROP, **KW).is_pup is False


def test_no_dog_visible_is_not_our_pup(monkeypatch):
    _stub(monkeypatch, {"is_pup": False, "confidence": 0.99, "dogs_visible": 0})
    assert identify.confirm_pup(CROP, **KW).is_pup is False


def test_request_sends_references_before_the_candidate(monkeypatch):
    """Ordering matters: the prompt says the LAST image is the one to verify."""
    cap: dict = {}
    _stub(monkeypatch, {"is_pup": True, "confidence": 0.9, "dogs_visible": 1}, capture=cap)
    identify.confirm_pup(CROP, **KW)
    parts = cap["body"]["contents"][0]["parts"]
    images = [p["inline_data"]["data"] for p in parts if "inline_data" in p]
    assert images[:2] == [base64.b64encode(r).decode() for r in REFS]
    assert images[-1] == base64.b64encode(CROP).decode()


def test_request_pins_deterministic_structured_output(monkeypatch):
    cap: dict = {}
    _stub(monkeypatch, {"is_pup": True, "confidence": 0.9, "dogs_visible": 1}, capture=cap)
    identify.confirm_pup(CROP, **KW)
    cfg = cap["body"]["generationConfig"]
    assert cfg["temperature"] == 0.0
    assert cfg["responseMimeType"] == "application/json"
    assert "is_pup" in cfg["responseSchema"]["properties"]
    assert "gemini-2.5-flash-lite" in cap["url"]


def test_token_is_url_encoded_not_interpolated_raw(monkeypatch):
    cap: dict = {}
    monkeypatch.setenv("PUPWATCH_GEMINI_TOKEN", "abc/def+ghi")
    monkeypatch.setattr(identify, "reference_images", lambda: REFS)
    monkeypatch.setattr(
        identify, "_post",
        lambda url, body, timeout: (cap.__setitem__("url", url),
                                    _reply({"is_pup": True, "confidence": 0.9, "dogs_visible": 1}))[1],
    )
    identify.confirm_pup(CROP, **KW)
    assert "abc/def+ghi" not in cap["url"]
    assert "abc%2Fdef%2Bghi" in cap["url"]


def test_transport_error_fails_open_as_inconclusive(monkeypatch):
    monkeypatch.setenv("PUPWATCH_GEMINI_TOKEN", "key123")
    monkeypatch.setattr(identify, "reference_images", lambda: REFS)

    def boom(url, body, timeout):
        raise RuntimeError("gemini_status=503")

    monkeypatch.setattr(identify, "_post", boom)
    ident = identify.confirm_pup(CROP, **KW)
    assert ident.conclusive is False
    assert ident.skipped.startswith("error:")


def test_malformed_response_fails_open(monkeypatch):
    monkeypatch.setenv("PUPWATCH_GEMINI_TOKEN", "key123")
    monkeypatch.setattr(identify, "reference_images", lambda: REFS)
    monkeypatch.setattr(identify, "_post", lambda url, body, timeout: {"candidates": []})
    assert identify.confirm_pup(CROP, **KW).conclusive is False


def test_reference_images_read_from_local_paths(tmp_path, monkeypatch):
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    a.write_bytes(b"AAA")
    b.write_bytes(b"BBB")
    identify._reference_images_cached.cache_clear()
    monkeypatch.setenv("PUPWATCH_REFERENCE_URIS", f"{a},{b}")
    assert identify.reference_images() == (b"AAA", b"BBB")


def test_missing_reference_path_is_skipped_not_fatal(tmp_path, monkeypatch):
    a = tmp_path / "a.jpg"
    a.write_bytes(b"AAA")
    identify._reference_images_cached.cache_clear()
    monkeypatch.setenv("PUPWATCH_REFERENCE_URIS", f"{a},{tmp_path / 'nope.jpg'}")
    assert identify.reference_images() == (b"AAA",)


def test_no_reference_spec_is_empty(monkeypatch):
    identify._reference_images_cached.cache_clear()
    monkeypatch.delenv("PUPWATCH_REFERENCE_URIS", raising=False)
    assert identify.reference_images() == ()
