"""Vector emblems stamped into wrap textures.

Shapes are defined in a normalised box (x in -0.5..0.5, y in 0..1) and rasterised
with 4x supersampling so the edges stay clean at 1024px.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from . import paint

SUPERSAMPLE = 4

def _arc(p0, p1, bulge: float, steps: int = 14):
    """Quadratic Bezier from ``p0`` to ``p1``, bowed perpendicular by ``bulge``.

    Positive bulge bows towards the top of the emblem box, which is what carves
    the scooped membrane between a bat wing's finger points.
    """
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = x1 - x0, y1 - y0
    length = max((dx * dx + dy * dy) ** 0.5, 1e-9)
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    cx, cy = mx + (dy / length) * bulge, my - (dx / length) * bulge
    ts = [i / steps for i in range(1, steps + 1)]
    return [
        (
            (1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t**2 * x1,
            (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t**2 * y1,
        )
        for t in ts
    ]


# Right half of the bat, walked clockwise from the crown of the head to the tip
# of the tail. Each entry is (vertex, bulge-of-segment-leading-to-it).
_BAT_RIGHT = [
    ((0.075, 0.105), 0.030),  # head crown -> neck
    ((0.500, 0.000), 0.070),  # wing leading edge, bowed up to a sharp tip
    ((0.445, 0.150), 0.000),  # trailing edge under the tip
    ((0.398, 0.315), 0.000),  # finger 1
    ((0.348, 0.155), 0.026),  # membrane scoop
    ((0.256, 0.400), 0.000),  # finger 2
    ((0.192, 0.192), 0.034),
    ((0.118, 0.530), 0.000),  # finger 3
    ((0.098, 0.295), 0.020),
    ((0.000, 0.715), 0.000),  # tail point
]


def bat_outline() -> list[tuple[float, float]]:
    """Closed outline of the bat, normalised to fill x in -0.5..0.5, y in 0..1."""
    right = [(0.0, 0.062)]
    for vertex, bulge in _BAT_RIGHT:
        right.extend(_arc(right[-1], vertex, bulge) if bulge else [vertex])
    points = right + [(-x, y) for x, y in reversed(right[:-1])]

    lo = min(y for _, y in points)
    hi = max(y for _, y in points)
    return [(x, (y - lo) / (hi - lo)) for x, y in points]


def stamp(
    shape: tuple[int, int],
    outline: list[tuple[float, float]],
    cx: float,
    cy: float,
    width: float,
    height: float,
    flip: bool = False,
    rotate: int = 0,
) -> np.ndarray:
    """Rasterise a normalised outline into a 0..1 coverage mask on the canvas.

    ``rotate`` is needed because the atlas is not uniformly oriented: on the
    hood and fascias texture X runs across the car, but on the flanks texture X
    runs *up* the car body. Pass 90 for the left flank and -90 for the right so
    a badge stands upright on the door rather than lying on its side.
    """
    h, w = shape
    canvas = Image.new("L", (w * SUPERSAMPLE, h * SUPERSAMPLE), 0)
    points = []
    for nx, ny in outline:
        along = nx * width
        across = ((1.0 - ny) if flip else ny) - 0.5
        if rotate == 90:
            px, py = cx - across * height, cy + along
        elif rotate == -90:
            px, py = cx + across * height, cy + along
        else:
            px, py = cx + along, cy + across * height
        points.append((px * SUPERSAMPLE, py * SUPERSAMPLE))
    ImageDraw.Draw(canvas).polygon(points, fill=255)
    return np.asarray(canvas.resize((w, h), Image.LANCZOS), dtype=np.float64) / 255.0


def outlined(mask: np.ndarray, thickness: float = 2.0) -> np.ndarray:
    """A soft halo just outside ``mask``, for emblem pinstripes."""
    from scipy import ndimage

    solid = mask > 0.5
    outside = ndimage.distance_transform_edt(~solid)
    return np.clip(1.0 - np.abs(outside - thickness) / thickness, 0, 1) * (~solid)


def arc_reactor(
    shape: tuple[int, int], cx: float, cy: float, radius: float, flip: bool = False
) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(rgb, alpha)`` layers for a Mark-43 style arc reactor.

    Concentric rings plus the triangular core, with a soft cyan bloom that falls
    off outside the bezel so it reads as emitted light rather than a decal.
    """
    r = paint.radial(shape, cx, cy, radius)
    rgb = np.zeros(shape + (3,))
    alpha = np.zeros(shape)

    rings = [
        (0.00, 0.52, "#06131A"),  # core well
        (0.52, 0.60, "#7FF2FF"),  # inner emitter
        (0.60, 0.72, "#0A1E28"),  # coil recess
        (0.72, 0.80, "#F0C349"),  # gold ring
        (0.80, 0.88, "#0D0D10"),  # dark gap
        (0.88, 1.00, "#C8981F"),  # bezel
    ]
    for lo, hi, colour in rings:
        sel = paint.band(r, lo, hi, feather=0.012)
        rgb += paint.hex_rgb(colour) * sel[..., None]
        alpha = np.maximum(alpha, sel)

    # Coil blocks around the recess.
    ys, xs = np.mgrid[0 : shape[0], 0 : shape[1]]
    theta = np.arctan2(ys - cy, xs - cx)
    coils = paint.band(r, 0.62, 0.70, 0.012) * (np.cos(8 * theta) > 0.55)
    rgb = rgb * (1 - coils[..., None]) + paint.hex_rgb("#F5D97A") * coils[..., None]

    triangle = stamp(
        shape,
        [(0.0, 0.0), (0.5, 1.0), (-0.5, 1.0)],
        cx,
        cy,
        radius * 0.78,
        radius * 0.66,
        flip=flip,
    )
    triangle *= paint.band(r, 0.0, 0.55, 0.02)
    rgb = rgb * (1 - triangle[..., None]) + paint.hex_rgb("#E8FEFF") * triangle[..., None]
    alpha = np.maximum(alpha, triangle)

    bloom = np.clip(1.0 - (r - 1.0) / 0.30, 0, 1) * (r > 1.0)
    rgb += paint.hex_rgb("#2E6C7E") * (bloom**2)[..., None] * 0.9
    alpha = np.maximum(alpha, bloom**2 * 0.85)
    return np.clip(rgb, 0, 1), np.clip(alpha, 0, 1)


def composite(canvas: np.ndarray, rgb: np.ndarray, alpha: np.ndarray, mask: np.ndarray) -> None:
    """Alpha-composite an emblem layer, clipped to a panel mask."""
    a = (alpha * mask)[..., None]
    canvas *= 1 - a
    canvas += rgb * a
