"""Detect dogs/people in a yard frame and judge whether a dog is cream-coated.

Two things here are load-bearing and were chosen from measurement, not taste:

* **Tiling.** The pup renders ~55px tall at the far fence and ~110px in the
  foreground. Running the detector on the full frame *and* on overlapping yard
  tiles roughly doubles his apparent size in at least one pass. On a composite
  sweep of the real yard this lifted recall at >=55px to 12/12; the full frame
  alone missed several.
* **Model size.** A 9MB nano detector failed exactly in the 55-70px band that
  matters here, so we ship the larger model. It costs ~270ms per pass, which is
  irrelevant at one poll per minute.
"""

from __future__ import annotations

import io
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

from .config import COCO_DOG, COCO_PERSON, Settings

log = logging.getLogger("pup_watch")

DEFAULT_MODEL_PATH = "/app/models/detector.onnx"
_INPUT_SIZE = 640

_session: Any = None
_session_lock = threading.Lock()

Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class Detection:
    cls: int
    score: float
    box: Box  # x1, y1, x2, y2 in full-frame coordinates

    @property
    def height(self) -> float:
        return self.box[3] - self.box[1]

    @property
    def width(self) -> float:
        return self.box[2] - self.box[0]


@dataclass(frozen=True)
class CreamStats:
    fraction: float
    mean_brightness: float
    mean_saturation: float

    def passes(self, s: Settings) -> bool:
        return self.fraction >= s.cream_pixel_fraction_min


@dataclass(frozen=True)
class FrameVerdict:
    dogs: tuple[Detection, ...]
    persons: tuple[Detection, ...]
    cream: Optional[CreamStats]
    lone_cream_dog: bool
    reason: str

    @property
    def best_dog(self) -> Optional[Detection]:
        return max(self.dogs, key=lambda d: d.score, default=None)


def set_session(session: Any) -> None:
    """Tests: inject a fake ONNX session (or None to reset)."""
    global _session
    with _session_lock:
        _session = session


def _load_session() -> Any:
    global _session
    if _session is not None:
        return _session
    with _session_lock:
        if _session is not None:
            return _session
        import onnxruntime as ort  # imported lazily so tests need no runtime

        path = os.environ.get("PUPWATCH_MODEL_PATH", DEFAULT_MODEL_PATH)
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = int(os.environ.get("PUPWATCH_ORT_THREADS", "2"))
        _session = ort.InferenceSession(
            path, sess_options=opts, providers=["CPUExecutionProvider"]
        )
        log.info("pup-watch model_loaded path=%s", path)
        return _session


def _letterbox(im: Any, size: int) -> tuple[Any, float]:
    from PIL import Image

    w, h = im.size
    r = min(size / w, size / h)
    canvas = Image.new("RGB", (size, size), (114, 114, 114))
    canvas.paste(im.convert("RGB").resize((max(1, int(w * r)), max(1, int(h * r))), Image.BILINEAR), (0, 0))
    return canvas, r


def _run_once(im: Any, offset: tuple[int, int]) -> list[Detection]:
    import numpy as np

    session = _load_session()
    canvas, r = _letterbox(im, _INPUT_SIZE)
    x = np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
    name = session.get_inputs()[0].name
    raw = np.asarray(session.run(None, {name: x})[0]).reshape(-1, 6)
    ox, oy = offset
    out: list[Detection] = []
    for x1, y1, x2, y2, score, cls in raw:
        out.append(
            Detection(
                cls=int(cls),
                score=float(score),
                box=(x1 / r + ox, y1 / r + oy, x2 / r + ox, y2 / r + oy),
            )
        )
    return out


def _iou(a: Box, b: Box) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def dedupe(dets: Iterable[Detection], iou_threshold: float = 0.5) -> list[Detection]:
    """Greedy NMS so the same dog seen in two tiles counts once, not twice.

    Without this, tiling would inflate the dog count and trip the "he is only
    ever alone" veto on a single dog.
    """
    kept: list[Detection] = []
    for det in sorted(dets, key=lambda d: d.score, reverse=True):
        if any(k.cls == det.cls and _iou(k.box, det.box) >= iou_threshold for k in kept):
            continue
        kept.append(det)
    return kept


def cream_stats(im: Any, box: Box, settings: Optional[Settings] = None) -> CreamStats:
    """Share of the box that reads as a bright, desaturated (cream) coat."""
    import numpy as np

    s = settings or Settings()
    x1, y1, x2, y2 = (int(round(v)) for v in box)
    w, h = im.size
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 - x1 < 2 or y2 - y1 < 2:
        return CreamStats(0.0, 0.0, 0.0)
    # Trim the border: a tight box still contains turf at the corners.
    dx, dy = int((x2 - x1) * 0.12), int((y2 - y1) * 0.12)
    patch = im.convert("RGB").crop((x1 + dx, y1 + dy, max(x1 + dx + 1, x2 - dx), max(y1 + dy + 1, y2 - dy)))
    a = np.asarray(patch, dtype=np.float32)
    if a.size == 0:
        return CreamStats(0.0, 0.0, 0.0)
    brightness = a.mean(axis=2)
    saturation = a.max(axis=2) - a.min(axis=2)
    mask = (brightness > s.cream_brightness_min) & (saturation < s.cream_saturation_max)
    return CreamStats(
        fraction=float(mask.mean()),
        mean_brightness=float(brightness.mean()),
        mean_saturation=float(saturation.mean()),
    )


def analyse_frame(
    frame: bytes | Any,
    *,
    settings: Settings,
    regions: Sequence[tuple[int, int, int, int]] = (),
) -> FrameVerdict:
    """Full-frame + tiled detection, then apply the lone-cream-dog rule."""
    from PIL import Image

    im = frame if hasattr(frame, "size") else Image.open(io.BytesIO(frame))
    im = im.convert("RGB")

    raw: list[Detection] = _run_once(im, (0, 0))
    for region in regions:
        x1, y1, x2, y2 = region
        crop = im.crop((x1, y1, x2, y2))
        if crop.size[0] > 1 and crop.size[1] > 1:
            raw.extend(_run_once(crop, (x1, y1)))

    dogs = dedupe(
        d for d in raw
        if d.cls == COCO_DOG and d.score >= settings.dog_score_min and d.height >= settings.dog_min_box_px
    )
    persons = dedupe(
        d for d in raw if d.cls == COCO_PERSON and d.score >= settings.person_score_min
    )

    if not dogs:
        return FrameVerdict((), tuple(persons), None, False, "no_dog")
    if len(dogs) > settings.max_dogs:
        return FrameVerdict(tuple(dogs), tuple(persons), None, False, f"multiple_dogs n={len(dogs)}")

    best = max(dogs, key=lambda d: d.score)
    stats = cream_stats(im, best.box, settings)
    if not stats.passes(settings):
        return FrameVerdict(
            tuple(dogs), tuple(persons), stats, False,
            f"dog_not_cream fraction={stats.fraction:.2f}",
        )
    return FrameVerdict(tuple(dogs), tuple(persons), stats, True, "lone_cream_dog")


def crop_detection(frame: bytes | Any, box: Box, *, pad: float = 0.18) -> bytes:
    """JPEG crop around a detection, padded, for the identity check and the email."""
    from PIL import Image

    im = frame if hasattr(frame, "size") else Image.open(io.BytesIO(frame))
    im = im.convert("RGB")
    w, h = im.size
    x1, y1, x2, y2 = box
    px, py = (x2 - x1) * pad, (y2 - y1) * pad
    crop = im.crop((
        max(0, int(x1 - px)), max(0, int(y1 - py)),
        min(w, int(x2 + px)), min(h, int(y2 + py)),
    ))
    # Upscale small crops so the identity model has pixels to reason about.
    if crop.size[1] < 224:
        scale = 224 / max(1, crop.size[1])
        crop = crop.resize((int(crop.size[0] * scale), int(crop.size[1] * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    crop.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def annotate(frame: bytes | Any, dets: Sequence[Detection]) -> bytes:
    """Draw boxes on the frame so the email shows what actually fired."""
    from PIL import Image, ImageDraw

    im = frame if hasattr(frame, "size") else Image.open(io.BytesIO(frame))
    im = im.convert("RGB")
    draw = ImageDraw.Draw(im)
    for d in dets:
        colour = (255, 96, 0) if d.cls == COCO_DOG else (60, 160, 255)
        label = "dog" if d.cls == COCO_DOG else "person"
        draw.rectangle(d.box, outline=colour, width=3)
        draw.text((d.box[0] + 4, max(0, d.box[1] - 12)), f"{label} {d.score:.2f}", fill=colour)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return buf.getvalue()
