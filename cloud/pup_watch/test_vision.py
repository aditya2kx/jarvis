"""Detection plumbing: the lone-cream-dog rule, tile dedupe, cream scoring."""

from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from cloud.pup_watch import vision
from cloud.pup_watch.config import COCO_DOG, COCO_PERSON, Settings

S = Settings()
FRAME = (720, 576)
_R = min(640 / FRAME[0], 640 / FRAME[1])


class FakeSession:
    """Returns fixed detections, expressed in source-image coordinates."""

    def __init__(self, dets):
        self.dets = dets
        self.calls = 0

    def get_inputs(self):
        return [SimpleNamespace(name="images")]

    def run(self, _outputs, _feed):
        self.calls += 1
        rows = [[x1 * _R, y1 * _R, x2 * _R, y2 * _R, sc, cl] for x1, y1, x2, y2, sc, cl in self.dets]
        arr = np.array(rows, dtype=np.float32) if rows else np.zeros((0, 6), np.float32)
        return [arr]


class BlobSession:
    """A position-aware fake: finds the cream blob in whatever pixels it is given.

    FakeSession returns fixed coordinates, which cannot exercise the tile
    offset/letterbox mapping — it would report the same global box for every
    crop. This one behaves like a real detector, so a tiled run has to map
    tile-local boxes back to full-frame coordinates correctly for the dedupe to
    collapse them.
    """

    def __init__(self, score=0.70, min_pixels=40):
        self.score = score
        self.min_pixels = min_pixels
        self.calls = 0

    def get_inputs(self):
        return [SimpleNamespace(name="images")]

    def run(self, _outputs, feed):
        self.calls += 1
        x = next(iter(feed.values()))[0]  # (3, H, W) in 0..1
        brightness = x.mean(axis=0)
        saturation = x.max(axis=0) - x.min(axis=0)
        mask = (brightness > 0.6) & (saturation < 0.35)
        ys, xs = np.where(mask)
        if len(xs) < self.min_pixels:
            return [np.zeros((0, 6), np.float32)]
        row = [xs.min(), ys.min(), xs.max() + 1, ys.max() + 1, self.score, COCO_DOG]
        return [np.array([row], dtype=np.float32)]


@pytest.fixture(autouse=True)
def _reset_session():
    yield
    vision.set_session(None)


def _yard(colour=(90, 150, 70)):
    return Image.new("RGB", FRAME, colour)


def _paint(im, box, colour):
    im.paste(Image.new("RGB", (int(box[2] - box[0]), int(box[3] - box[1])), colour), (int(box[0]), int(box[1])))
    return im


CREAM = (238, 232, 214)
DARK_DOG = (60, 48, 40)


def test_lone_cream_dog_passes():
    box = (300, 300, 340, 380)
    im = _paint(_yard(), box, CREAM)
    vision.set_session(FakeSession([(*box, 0.71, COCO_DOG)]))
    v = vision.analyse_frame(im, settings=S)
    assert v.lone_cream_dog is True
    assert v.reason == "lone_cream_dog"
    assert len(v.dogs) == 1
    assert v.best_dog.score == pytest.approx(0.71, abs=1e-3)


def test_people_present_does_not_block_a_sighting():
    """Staff in the yard is normal and must not suppress the alert."""
    box = (300, 300, 340, 380)
    im = _paint(_yard(), box, CREAM)
    vision.set_session(FakeSession([
        (*box, 0.71, COCO_DOG),
        (500, 250, 540, 400, 0.80, COCO_PERSON),
        (100, 250, 140, 400, 0.60, COCO_PERSON),
    ]))
    v = vision.analyse_frame(im, settings=S)
    assert v.lone_cream_dog is True
    assert len(v.persons) == 2


def test_two_dogs_is_a_hard_veto():
    """He is only ever let out alone, so a second dog means it is not him."""
    a, b = (300, 300, 340, 380), (500, 300, 540, 380)
    im = _paint(_paint(_yard(), a, CREAM), b, CREAM)
    vision.set_session(FakeSession([(*a, 0.80, COCO_DOG), (*b, 0.75, COCO_DOG)]))
    v = vision.analyse_frame(im, settings=S)
    assert v.lone_cream_dog is False
    assert "multiple_dogs" in v.reason


def test_dark_dog_is_rejected_by_the_cream_gate():
    box = (300, 300, 340, 380)
    im = _paint(_yard(), box, DARK_DOG)
    vision.set_session(FakeSession([(*box, 0.90, COCO_DOG)]))
    v = vision.analyse_frame(im, settings=S)
    assert v.lone_cream_dog is False
    assert "dog_not_cream" in v.reason


def test_low_score_detection_ignored():
    box = (300, 300, 340, 380)
    im = _paint(_yard(), box, CREAM)
    vision.set_session(FakeSession([(*box, 0.10, COCO_DOG)]))
    assert vision.analyse_frame(im, settings=S).reason == "no_dog"


def test_tiny_box_ignored_even_when_confident():
    """Below ~40px the detector is unreliable, so small boxes are not trusted."""
    box = (300, 300, 315, 320)
    im = _paint(_yard(), box, CREAM)
    vision.set_session(FakeSession([(*box, 0.95, COCO_DOG)]))
    assert vision.analyse_frame(im, settings=S).reason == "no_dog"


def test_empty_yard_yields_no_dog():
    vision.set_session(FakeSession([]))
    v = vision.analyse_frame(_yard(), settings=S)
    assert (v.reason, v.lone_cream_dog, v.dogs) == ("no_dog", False, ())


def test_tiling_runs_extra_passes_and_dedupes_to_one_dog():
    """Tiling must not turn one dog into two and trip the multi-dog veto."""
    box = (300, 380, 340, 460)  # wholly inside the lower tile
    im = _paint(_yard(), box, CREAM)
    fake = BlobSession()
    vision.set_session(fake)
    regions = ((0, 0, 720, 330), (0, 250, 720, 576))
    v = vision.analyse_frame(im, settings=S, regions=regions)
    assert fake.calls == 3  # full frame + two tiles
    assert len(v.dogs) == 1
    assert v.lone_cream_dog is True


def test_tiled_box_maps_back_to_full_frame_coordinates():
    """The box reported from a tile must land on the dog in the full frame."""
    box = (300, 380, 340, 460)
    im = _paint(_yard(), box, CREAM)
    vision.set_session(BlobSession())
    v = vision.analyse_frame(im, settings=S, regions=((0, 250, 720, 576),))
    got = v.best_dog.box
    for got_v, want_v in zip(got, box):
        assert got_v == pytest.approx(want_v, abs=3)


def test_dedupe_keeps_distinct_dogs_but_collapses_overlaps():
    same_a = vision.Detection(COCO_DOG, 0.9, (100, 100, 140, 180))
    same_b = vision.Detection(COCO_DOG, 0.6, (104, 103, 143, 182))
    other = vision.Detection(COCO_DOG, 0.7, (500, 100, 540, 180))
    kept = vision.dedupe([same_a, same_b, other])
    assert len(kept) == 2
    assert {round(d.score, 2) for d in kept} == {0.9, 0.7}


def test_dedupe_does_not_merge_across_classes():
    dog = vision.Detection(COCO_DOG, 0.9, (100, 100, 140, 180))
    person = vision.Detection(COCO_PERSON, 0.9, (100, 100, 140, 180))
    assert len(vision.dedupe([dog, person])) == 2


def test_cream_stats_separates_cream_coat_from_turf_and_dark_coat():
    cream = vision.cream_stats(Image.new("RGB", (60, 60), CREAM), (0, 0, 60, 60))
    turf = vision.cream_stats(Image.new("RGB", (60, 60), (90, 150, 70)), (0, 0, 60, 60))
    dark = vision.cream_stats(Image.new("RGB", (60, 60), DARK_DOG), (0, 0, 60, 60))
    assert cream.fraction > 0.9 and cream.passes(S)
    assert turf.fraction < 0.05 and not turf.passes(S)
    assert dark.fraction < 0.05 and not dark.passes(S)


def test_cream_stats_handles_degenerate_box():
    stats = vision.cream_stats(_yard(), (10, 10, 11, 11))
    assert stats.fraction == 0.0


def test_cream_stats_clamps_out_of_bounds_box():
    stats = vision.cream_stats(Image.new("RGB", (60, 60), CREAM), (-50, -50, 500, 500))
    assert stats.fraction > 0.9


def test_cream_stats_honours_a_passed_in_settings_override():
    """Regression: cream_stats used to ignore its settings arg and always
    thresholded against Settings() defaults, so a Firestore overlay of
    cream_brightness_min/cream_saturation_max had no effect."""
    dim = (140, 130, 120)  # passes a loosened threshold, fails the default
    default = vision.cream_stats(Image.new("RGB", (40, 40), dim), (0, 0, 40, 40))
    assert not default.passes(S)

    loose = Settings(cream_brightness_min=100.0, cream_saturation_max=150.0)
    loosened = vision.cream_stats(Image.new("RGB", (40, 40), dim), (0, 0, 40, 40), loose)
    assert loosened.passes(loose)


def test_analyse_frame_applies_the_overlaid_cream_thresholds():
    """End-to-end: a custom Settings must change the lone_cream_dog verdict."""
    box = (300, 300, 340, 380)
    dim = (140, 130, 120)
    im = _paint(_yard(), box, dim)
    vision.set_session(FakeSession([(*box, 0.71, COCO_DOG)]))
    assert vision.analyse_frame(im, settings=S).lone_cream_dog is False

    loose = Settings(cream_brightness_min=100.0, cream_saturation_max=150.0, cream_pixel_fraction_min=0.30)
    vision.set_session(FakeSession([(*box, 0.71, COCO_DOG)]))
    assert vision.analyse_frame(im, settings=loose).lone_cream_dog is True


def test_crop_detection_upscales_small_crops_for_the_identity_check():
    im = _paint(_yard(), (300, 300, 340, 380), CREAM)
    out = vision.crop_detection(im, (300, 300, 340, 380))
    import io

    assert Image.open(io.BytesIO(out)).size[1] >= 224


def test_annotate_returns_a_jpeg_of_the_same_frame_size():
    import io

    im = _yard()
    out = vision.annotate(im, [vision.Detection(COCO_DOG, 0.8, (300, 300, 340, 380))])
    assert Image.open(io.BytesIO(out)).size == FRAME
