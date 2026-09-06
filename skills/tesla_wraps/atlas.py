"""UV atlas parsing for Tesla custom-wrap templates.

A template PNG is a 1024x1024 UV layout: opaque white islands (one per body
panel) separated by transparent gutters. This module labels those islands and
gives each one a semantic name plus a local coordinate frame, so designs can be
expressed as "gold band along the beltline of the rear door" instead of raw
pixel arithmetic.

Frame convention (verified against modely-2025-premium):

* texture ``y`` runs front-of-car (0) to rear-of-car (1023)
* on the left half of the atlas, increasing ``x`` moves *up* the car body
  (rocker -> beltline); the right half mirrors it
* the large transparent region in the middle is the panoramic glass roof and is
  deliberately not part of the atlas
"""

from __future__ import annotations

import dataclasses
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

TEMPLATE_BASE_URL = "https://raw.githubusercontent.com/teslamotors/custom-wraps/main"
CACHE_DIR = Path(__file__).parent / ".template_cache"

# Every trim unwraps its body differently, so panel names are only meaningful
# for layouts this module has actually been checked against. A 2026 Model Y
# Dual Motor (i.e. Long Range AWD, the "Premium" trim) uses the first one.
SUPPORTED_VARIANTS = ("modely-2025-premium",)
EXPECTED_ISLANDS = 20

# Panels grouped by how a design usually treats them.
SIDE_PANELS = (
    "front_fender_l",
    "front_fender_r",
    "front_door_l",
    "front_door_r",
    "rear_door_l",
    "rear_door_r",
    "quarter_panel_l",
    "quarter_panel_r",
)
TOP_PANELS = ("hood", "rear_hatch")
TRIM_PANELS = (
    "mirror_l",
    "mirror_r",
    "roof_rail_l",
    "roof_rail_r",
    "rail_rear_l",
    "rail_rear_r",
)
FASCIA_PANELS = ("front_fascia", "rear_fascia", "rear_corner_l", "rear_corner_r")

ALL_PANELS = SIDE_PANELS + TOP_PANELS + TRIM_PANELS + FASCIA_PANELS


@dataclasses.dataclass(frozen=True)
class Island:
    """One UV island: a body panel's footprint in texture space."""

    name: str
    mask: np.ndarray  # bool, full canvas size
    bbox: tuple[int, int, int, int]  # x0, y0, x1, y1 (exclusive stops)
    centroid: tuple[float, float]  # cx, cy
    area: int

    @property
    def is_left(self) -> bool:
        return self.centroid[0] < 512

    def frame(self, shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
        """Return ``(u, v)`` coordinate grids for this island.

        ``u`` is longitudinal: 0.0 at the nose of the car, 1.0 at the tail. It is
        global so a front-to-back gradient stays continuous across panels.

        ``v`` is transverse and normalised *within this panel's own bbox*: 0.0 at
        the beltline (top of the body side), 1.0 at the rocker. Per-panel
        normalisation is what keeps a "beltline stripe" on the beltline even
        though the door panels are not axis-aligned rectangles. For centre
        panels (hood, fascias) ``v`` is the lateral position instead, 0.0 at the
        car's centreline and 1.0 at the outer edge.
        """
        h, w = shape
        ys, xs = np.mgrid[0:h, 0:w]
        u = ys / (h - 1)

        x0, _, x1, _ = self.bbox
        span = max(x1 - 1 - x0, 1)
        if abs(self.centroid[0] - 512) < 60:
            # Centre panel: measure outward from the car's centreline.
            v = np.abs(xs - 512) / max(abs(x1 - 512), abs(x0 - 512), 1)
        elif self.is_left:
            v = (x1 - 1 - xs) / span
        else:
            v = (xs - x0) / span
        return u, np.clip(v, 0.0, 1.0)


class Atlas:
    """A parsed template: named islands plus the seam-closing helper."""

    def __init__(self, template: Image.Image):
        self.size = template.size
        rgba = np.array(template.convert("RGBA"))
        interior = (rgba[..., 3] > 128) & (rgba[..., :3].mean(axis=2) > 200)
        labels, count = ndimage.label(interior)
        if count != EXPECTED_ISLANDS:
            raise ValueError(
                f"template has {count} UV islands, expected {EXPECTED_ISLANDS}. "
                f"The panel names in this module are only calibrated for "
                f"{', '.join(SUPPORTED_VARIANTS)}; other trims unwrap differently "
                f"and would need their own classification."
            )
        self.islands = _classify(labels, count)
        self._labels = labels

    def __getitem__(self, name: str) -> Island:
        return self.islands[name]

    def mask_of(self, *names: str) -> np.ndarray:
        out = np.zeros(self.size[::-1], dtype=bool)
        for name in names:
            out |= self.islands[name].mask
        return out

    @property
    def painted(self) -> np.ndarray:
        """Union of every island — everything a design is allowed to colour."""
        return self._labels > 0

    def close_seams(self, rgb: np.ndarray, alpha: np.ndarray, radius: int = 6):
        """Bleed panel colour into the gutters between islands.

        The template's 2px gutters would otherwise render as hairline gaps on the
        car. Each gutter pixel within ``radius`` takes the colour of its nearest
        painted pixel.
        """
        empty = alpha == 0
        _, (iy, ix) = ndimage.distance_transform_edt(empty, return_indices=True)
        dist = ndimage.distance_transform_edt(empty)
        grow = empty & (dist <= radius)
        rgb[grow] = rgb[iy[grow], ix[grow]]
        alpha[grow] = 255
        return rgb, alpha


def _classify(labels: np.ndarray, count: int) -> dict[str, Island]:
    """Assign semantic names to the 20 islands from their geometry alone."""
    raw = []
    for idx in range(1, count + 1):
        mask = labels == idx
        ys, xs = np.nonzero(mask)
        raw.append(
            {
                "mask": mask,
                "bbox": (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1),
                "centroid": (float(xs.mean()), float(ys.mean())),
                "area": int(mask.sum()),
            }
        )

    h, w = labels.shape
    centre = [r for r in raw if abs(r["centroid"][0] - w / 2) < 60]
    centre_ids = {id(r) for r in centre}
    flanks = [r for r in raw if id(r) not in centre_ids]

    named: dict[str, dict] = {}

    # Centre column, front to back: front fascia, hood, rear hatch, rear fascia.
    for name, rec in zip(
        ("front_fascia", "hood", "rear_hatch", "rear_fascia"),
        sorted(centre, key=lambda r: r["centroid"][1]),
    ):
        named[name] = rec

    left = sorted((r for r in flanks if r["centroid"][0] < w / 2), key=lambda r: r["centroid"][1])
    right = sorted((r for r in flanks if r["centroid"][0] >= w / 2), key=lambda r: r["centroid"][1])

    def aspect(rec):
        x0, y0, x1, y1 = rec["bbox"]
        return (y1 - y0) / max(x1 - x0, 1)

    for side, group in (("l", left), ("r", right)):
        mirrors = [r for r in group if r["area"] < 4000 and aspect(r) < 1.5]
        rails = sorted((r for r in group if aspect(r) > 2.4), key=lambda r: r["centroid"][1])
        taken = {id(r) for r in mirrors} | {id(r) for r in rails}
        body = sorted(
            (r for r in group if id(r) not in taken), key=lambda r: r["centroid"][1]
        )
        if len(mirrors) != 1 or len(rails) != 2 or len(body) != 5:
            raise ValueError(
                f"unexpected {side}-side island split: "
                f"{len(mirrors)} mirror, {len(rails)} rail, {len(body)} body"
            )
        named[f"mirror_{side}"] = mirrors[0]
        named[f"roof_rail_{side}"], named[f"rail_rear_{side}"] = rails
        body_names = ("front_fender", "front_door", "rear_door", "quarter_panel", "rear_corner")
        for name, rec in zip(body_names, body):
            named[f"{name}_{side}"] = rec

    missing = set(ALL_PANELS) - set(named)
    if missing:
        raise ValueError(f"template classification missed panels: {sorted(missing)}")
    return {name: Island(name=name, **rec) for name, rec in named.items()}


def load_template(variant: str = SUPPORTED_VARIANTS[0]) -> Atlas:
    """Fetch (and cache) a template from teslamotors/custom-wraps, then parse it."""
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / f"{variant}.png"
    if not path.exists():
        url = f"{TEMPLATE_BASE_URL}/{variant}/template.png"
        with urllib.request.urlopen(url, timeout=30) as resp:
            path.write_bytes(resp.read())
    return Atlas(Image.open(path))
