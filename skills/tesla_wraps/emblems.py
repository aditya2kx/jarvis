"""Vector emblems stamped into wrap textures.

Shapes are defined in a normalised box (x in -0.5..0.5, y in 0..1) and rasterised
with 4x supersampling so the edges stay clean at 1024px.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from . import paint

SUPERSAMPLE = 4

# Right half of the wide, angular Nolan-trilogy bat, walked from the tail point
# up the inner edge to the wing tip and back along the top to the ear notch.
# Traced from a reference silhouette, simplified, then mirrored below so the
# emblem is exactly symmetric. Height is 0.3385 of the width — the shape is far
# flatter than a classic comic bat, and getting that ratio wrong is the single
# most obvious way to make it look like fan art.
BAT_ASPECT = 0.3385

_BAT_RIGHT = [
    (0.0000, 1.0000), (0.0190, 0.8756), (0.0345, 0.8096), (0.0681, 0.7157),
    (0.0836, 0.6853), (0.1112, 0.6447), (0.1147, 0.6447), (0.1198, 0.6345),
    (0.1233, 0.6345), (0.1250, 0.6294), (0.1284, 0.6294), (0.1353, 0.6193),
    (0.1405, 0.6193), (0.1474, 0.6091), (0.1526, 0.6091), (0.1543, 0.6041),
    (0.1612, 0.6041), (0.1629, 0.5990), (0.1698, 0.5990), (0.1716, 0.5939),
    (0.1784, 0.5939), (0.1802, 0.5888), (0.1905, 0.5888), (0.1922, 0.5838),
    (0.2026, 0.5838), (0.2043, 0.5787), (0.2216, 0.5787), (0.2233, 0.5736),
    (0.2509, 0.5736), (0.2526, 0.5685), (0.3060, 0.5685), (0.3078, 0.5736),
    (0.3422, 0.5736), (0.3440, 0.5787), (0.3474, 0.5787), (0.3414, 0.4898),
    (0.3431, 0.4036), (0.3500, 0.3376), (0.3672, 0.2513), (0.3888, 0.1827),
    (0.4078, 0.1371), (0.4284, 0.0964), (0.4647, 0.0406), (0.4681, 0.0406),
    (0.4853, 0.0152), (0.4888, 0.0152), (0.4957, 0.0051), (0.4991, 0.0051),
    (0.4991, 0.0000), (0.1647, 0.0000), (0.1586, 0.0178), (0.1534, 0.0838),
    (0.1448, 0.1396), (0.1319, 0.1777), (0.1284, 0.1827), (0.1250, 0.1827),
    (0.1147, 0.1980), (0.1095, 0.1980), (0.1078, 0.2030), (0.1026, 0.2030),
    (0.1009, 0.2081), (0.0957, 0.2081), (0.0940, 0.2132), (0.0853, 0.2132),
    (0.0836, 0.2183), (0.0750, 0.2183), (0.0733, 0.2234), (0.0422, 0.2234),
    (0.0362, 0.2107), (0.0293, 0.1244), (0.0267, 0.0305), (0.0164, 0.1320),
]


def bat_outline() -> list[tuple[float, float]]:
    """Closed outline of the bat, normalised to x in -0.5..0.5, y in 0..1."""
    return list(_BAT_RIGHT) + [(-x, y) for x, y in reversed(_BAT_RIGHT)]


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
