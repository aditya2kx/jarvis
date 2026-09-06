"""Vector emblems stamped into wrap textures.

Shapes are defined in a normalised box (x in -0.5..0.5, y in 0..1) and rasterised
with 4x supersampling so the edges stay clean at 1024px.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

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


# The helmet, traced the same way as the bat: segment a flat front-elevation
# reference into its shell / plate / emitter regions, contour each region,
# simplify, and keep the right half so the mirror is exact.
#
# What makes the helmet read as *the* helmet is not the silhouette, it is the
# plate break-up: a brow plate with a V notch driven down into it from the
# crown, a cheek plate that wraps under both eye slits, a separate chin plate,
# and a crescent jaw plate either side. The red between them is helmet shell
# showing through, and those gaps are the mouth and the eye sockets. Draw the
# outline alone and you get a gold egg; draw the plates and the rest follows.
FACE_ASPECT = 1.4832

_SHELL_RIGHT = [
    (+0.0000, 1.0000), (+0.0826, 0.9979), (+0.1835, 0.9773), (+0.2508, 0.9526), (+0.2997, 0.9278),
    (+0.3379, 0.9021), (+0.3869, 0.8588), (+0.4235, 0.8113), (+0.4480, 0.7515),
    (+0.4541, 0.7021), (+0.4541, 0.6443), (+0.5000, 0.6134), (+0.5000, 0.5536),
    (+0.4908, 0.4464), (+0.4939, 0.3948), (+0.4878, 0.3619), (+0.4434, 0.3361),
    (+0.3777, 0.2918), (+0.3716, 0.2835), (+0.3685, 0.1722), (+0.2492, 0.0670),
    (+0.2278, 0.0526), (+0.1911, 0.0155), (+0.1682, 0.0000),
]

_BROW_RIGHT = [
    (+0.0841, 0.6876), (+0.1575, 0.8918), (+0.1636, 0.9062), (+0.1743, 0.9072),
    (+0.2752, 0.8701), (+0.3746, 0.8031), (+0.3502, 0.7454), (+0.3379, 0.6876),
    (+0.3379, 0.5206), (+0.2661, 0.4928), (+0.2080, 0.4784), (+0.1284, 0.4660),
    (+0.0336, 0.4598),
]

_CHEEK_RIGHT = [
    (+0.1009, 0.4433), (+0.1376, 0.4124), (+0.2049, 0.4124), (+0.2752, 0.4206),
    (+0.3532, 0.4402), (+0.3654, 0.4670), (+0.3716, 0.5082), (+0.3654, 0.5701),
    (+0.3716, 0.7124), (+0.3884, 0.7546), (+0.3991, 0.7454), (+0.3991, 0.6670),
    (+0.4113, 0.5928), (+0.4297, 0.5371), (+0.4602, 0.4794), (+0.4358, 0.4464),
    (+0.3563, 0.3742), (+0.2783, 0.3216), (+0.1942, 0.2753), (+0.1560, 0.1856),
]

_CHIN_RIGHT = [
    (+0.1514, 0.1660), (+0.1361, 0.0732), (+0.1284, 0.0680), (+0.1009, 0.0660),
]

# Crescent jaw plate — a closed loop that lives entirely on the right.
_JAW_RIGHT = [
    (+0.2141, 0.1196), (+0.1927, 0.1196), (+0.1881, 0.1227), (+0.1789, 0.1660),
    (+0.2003, 0.2361), (+0.2064, 0.2402), (+0.2187, 0.2649), (+0.2905, 0.3052),
    (+0.3364, 0.3361), (+0.3379, 0.3309), (+0.3287, 0.3247), (+0.3257, 0.3144),
    (+0.3012, 0.2856), (+0.2890, 0.2608), (+0.2706, 0.2381), (+0.2584, 0.2093),
    (+0.2462, 0.1928), (+0.2462, 0.1845), (+0.2401, 0.1804), (+0.2278, 0.1557),
    (+0.2278, 0.1433), (+0.2217, 0.1392), (+0.2217, 0.1309),
]

# Right eye slit, also a closed loop on one side only.
_EYE_RIGHT = [
    (+0.1590, 0.4268), (+0.1453, 0.4299), (+0.1315, 0.4474), (+0.1529, 0.4515),
    (+0.1713, 0.4495), (+0.2385, 0.4619), (+0.3410, 0.4959), (+0.3379, 0.4588),
    (+0.3150, 0.4474), (+0.2997, 0.4474), (+0.2446, 0.4330),
]


def _renormalise(points: list[tuple[float, float]]) -> tuple[list, float]:
    """Rescale a shape into its own box: x in -0.5..0.5, y in 0..1."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    boxed = [((x - min(xs)) / w - 0.5, (y - min(ys)) / h) for x, y in points]
    return boxed, h / w


# The nose fascia wears the helmet's own eye, lifted out of the faceplate and
# rescaled, so the two never drift apart. x runs inner (-0.5) to outer (0.5).
_SLIT, SLIT_ASPECT = _renormalise(_EYE_RIGHT)


def slit_outline(mirror: bool = False) -> list[tuple[float, float]]:
    """Closed eye-slit outline; ``mirror`` flips it for the car's other side."""
    return [(-x, y) for x, y in _SLIT] if mirror else list(_SLIT)


def _closed(right: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Mirror a right-half profile into a closed, exactly symmetric outline."""
    return list(right) + [(-x, y) for x, y in reversed(right)]


def face_outline() -> list[tuple[float, float]]:
    """Closed helmet silhouette, normalised to x in -0.5..0.5, y in 0..1."""
    return _closed(_SHELL_RIGHT)


def face_plates() -> list[list[tuple[float, float]]]:
    """Closed outlines of the five gold plates, in the same normalised box."""
    mirrored = [(-x, y) for x, y in _JAW_RIGHT]
    return [
        _closed(_BROW_RIGHT),
        _closed(_CHEEK_RIGHT),
        _closed(_CHIN_RIGHT),
        list(_JAW_RIGHT),
        mirrored,
    ]


def face_eyes() -> list[list[tuple[float, float]]]:
    """Closed outlines of the two eye slits, in the same normalised box."""
    return [list(_EYE_RIGHT), [(-x, y) for x, y in _EYE_RIGHT]]


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
    runs *up* the car body — towards larger X on the left of the car and
    smaller X on the right. Pass -90 on the left flank and 90 on the right so a
    badge stands upright on the door rather than lying on its side.
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
    shape: tuple[int, int],
    cx: float,
    cy: float,
    radius: float,
    flip: bool = False,
    rotate: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(rgb, alpha)`` layers for a Mark-43 style arc reactor.

    Concentric rings plus the triangular core, with a soft cyan bloom that falls
    off outside the bezel so it reads as emitted light rather than a decal.

    Only the core triangle has an orientation, but it has to be given one:
    texture X runs up the car on the flanks, so a reactor stamped flat onto a
    door ends up with its apex pointing at the bumper. Pass ``rotate`` the same
    way as ``stamp``.
    """
    r = paint.radial(shape, cx, cy, radius)
    rgb = np.zeros(shape + (3,))
    alpha = np.zeros(shape)

    rings = [
        (0.00, 0.52, "#071119"),  # core well
        (0.52, 0.60, "#C4F8FF"),  # inner emitter
        (0.60, 0.72, "#0B1C24"),  # coil recess
        (0.72, 0.80, "#EBD293"),  # gold ring
        (0.80, 0.88, "#0C0D10"),  # dark gap
        (0.88, 1.00, "#C0A05A"),  # bezel
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
        [(0.0, 1.0), (0.5, 0.0), (-0.5, 0.0)],
        cx,
        cy,
        radius * 0.78,
        radius * 0.66,
        flip=flip,
        rotate=rotate,
    )
    triangle *= paint.band(r, 0.0, 0.55, 0.02)
    rgb = rgb * (1 - triangle[..., None]) + paint.hex_rgb("#E8FEFF") * triangle[..., None]
    alpha = np.maximum(alpha, triangle)

    bloom = np.clip(1.0 - (r - 1.0) / 0.30, 0, 1) * (r > 1.0)
    rgb += paint.hex_rgb("#3E93AB") * (bloom**2)[..., None] * 0.9
    alpha = np.maximum(alpha, bloom**2 * 0.85)
    return np.clip(rgb, 0, 1), np.clip(alpha, 0, 1)


def _bevel(mask: np.ndarray, width: float, flip: bool) -> np.ndarray:
    """-1..1 lighting term: +1 on the edges of ``mask`` that face the crown.

    Sobel along image Y gives the edge normal; the sign flips with ``flip``
    because the helmet's own up is whichever way the atlas has it lying.
    """
    blurred = ndimage.gaussian_filter(mask, sigma=max(width * 0.012, 0.8))
    lift = ndimage.sobel(blurred, axis=0) * (1.0 if flip else -1.0)
    return lift / max(np.abs(lift).max(), 1e-9)


def faceplate(
    shape: tuple[int, int], cx: float, cy: float, width: float, flip: bool = False
) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(rgb, alpha)`` layers for a Mark-43 helmet.

    Shaded from the plate geometry rather than from hand-placed highlights:
    every gold plate is domed away from its own outline and bevelled bright
    along whichever of its edges faces the crown, so the brow, cheeks, jaw and
    chin catch the light because of where their edges are. The red shell
    showing between them is what draws the eye sockets and the mouth.
    """
    height = width * FACE_ASPECT

    def poly(points: list[tuple[float, float]]) -> np.ndarray:
        return stamp(shape, points, cx, cy, width, height, flip=flip)

    shell = poly(face_outline())
    inside = shell > 0.5
    plates = np.clip(sum(poly(p) for p in face_plates()), 0, 1)
    eyes = np.clip(sum(poly(e) for e in face_eyes()), 0, 1) * inside

    RED_HI, RED, RED_DEEP = "#C9262C", "#8E1218", "#3B080D"
    GOLD_HI, GOLD, GOLD_DEEP, GOLD_SHADOW = "#FBEEC0", "#D9B15A", "#8E6A28", "#3F2C12"

    # Shell first: candy red, darkest down in the sockets and the mouth, which
    # are simply the parts of it the plates do not cover.
    shell_lift = _bevel(shell, width, flip)
    rgb = paint.ramp(
        np.clip(0.74 + 0.34 * shell_lift, 0, 1),
        [(0.00, "#1A0407"), (0.30, RED_DEEP), (0.68, RED), (1.00, RED_HI)],
    )
    recess = np.clip(1.0 - paint.edge_distance(plates > 0.5) / max(width * 0.042, 2.5), 0, 1)
    rgb *= (1 - 0.60 * recess * (plates < 0.5))[..., None]

    # Plates: dome + bevel, with a hard dark line right at the plate edge so
    # each one reads as a separate piece of armour and not as one gold field.
    solid = plates > 0.5
    ys = np.mgrid[0 : shape[0], 0 : shape[1]][0]
    ny = (ys - cy) / height + 0.5
    if flip:
        ny = 1.0 - ny
    dome = np.clip(paint.edge_distance(solid) / max(width * 0.065, 2.5), 0, 1)
    gold = paint.ramp(
        np.clip(0.14 + 0.30 * dome + 0.50 * _bevel(plates, width, flip) + 0.22 * np.clip(ny, 0, 1), 0, 1),
        [(0.00, GOLD_SHADOW), (0.26, GOLD_DEEP), (0.62, GOLD), (0.90, GOLD_HI), (1.00, "#FFFBEA")],
    )
    lip = np.clip(1.0 - paint.edge_distance(solid) / 2.2, 0, 1) * solid
    gold *= (1 - 0.45 * lip)[..., None]
    rgb = rgb * (1 - plates[..., None]) + gold * plates[..., None]

    # Eyes: hard-edged emitter plus a bloom that spills onto the gold — the
    # spill is what sells them as light instead of paint.
    glow = ndimage.gaussian_filter(eyes, sigma=max(width * 0.026, 1.0))
    glow /= max(glow.max(), 1e-9)
    rgb += paint.hex_rgb("#5CD2EC") * (0.66 * glow**1.5)[..., None]
    rgb = rgb * (1 - eyes[..., None]) + paint.hex_rgb("#EAFDFF") * eyes[..., None]

    # Rolled edge, so the silhouette stays legible against the hood.
    rim = np.clip(1.0 - paint.edge_distance(inside) / 3.0, 0, 1) * inside
    rgb *= (1 - 0.50 * rim)[..., None]

    alpha = np.clip(shell + 0.45 * glow * inside, 0, 1)
    return np.clip(rgb, 0, 1), alpha


def hud(shape: tuple[int, int], cx: float, cy: float, radius: float) -> np.ndarray:
    """JARVIS targeting reticle as a 0..1 coverage mask.

    Everything is a hairline: the HUD in the films is drawn in single-pixel
    strokes and reads as projected light precisely because it never gains
    weight. Widths are in pixels so that holds at any reticle size.

    Nothing is drawn inside 0.72 of the radius — a reticle is drawn *around*
    something, and here that something is an arc reactor of 0.70 the radius,
    which would bury any inner detail.

    The grammar is JARVIS's, not a generic crosshair's: rings are broken into
    segments, the frame is four square corner brackets rather than a circle,
    and the only heavy strokes are the two gauge arcs. A HUD made of unbroken
    concentric circles reads as a clock face.
    """
    ys, xs = np.mgrid[0 : shape[0], 0 : shape[1]]
    mask = np.zeros(shape)

    def add(layer: np.ndarray, level: float) -> None:
        nonlocal mask
        mask = np.maximum(mask, level * layer)

    def bar(x0: float, x1: float, y0: float, y1: float) -> np.ndarray:
        return paint.band(xs, x0, x1, 0.9) * paint.band(ys, y0, y1, 0.9)

    # Segmented outer ring, hairline inner ring, and the two gauge arcs. Strokes
    # below ~2.4px never reach full value once ``ring`` has feathered both of
    # its edges, so a thinner line here just fades rather than sharpening.
    add(paint.ring(shape, cx, cy, radius, 2.4) * paint.dashes(shape, cx, cy, 48, 0.5), 0.48)
    add(paint.ring(shape, cx, cy, radius * 0.930, 2.4), 0.26)
    for start in (24.0, 204.0):
        add(paint.ring(shape, cx, cy, radius * 0.862, 2.8, start, 96.0), 0.85)

    # Tick ladder outside the gauges, with a heavier mark on each quadrant.
    add(paint.ticks(shape, cx, cy, radius * 0.952, radius * 0.994, 36), 0.38)
    add(paint.ticks(shape, cx, cy, radius * 0.930, radius * 1.010, 4, width=2.4, phase_deg=45.0), 0.70)

    # Square corner brackets: the frame JARVIS puts round a tracked object.
    side, arm, stroke = radius * 0.800, radius * 0.30, 2.2
    for sx in (-1.0, 1.0):
        for sy in (-1.0, 1.0):
            px, py = cx + sx * side, cy + sy * side
            add(bar(*sorted((px, px - sx * arm)), py - stroke / 2, py + stroke / 2), 0.90)
            add(bar(px - stroke / 2, px + stroke / 2, *sorted((py, py - sy * arm))), 0.90)

    # Crosshair, in the gap between the reactor's bezel and the first ring.
    add(paint.ticks(shape, cx, cy, radius * 0.725, radius * 0.800, 4, width=2.2), 0.70)
    return np.clip(mask, 0, 1)


def composite(canvas: np.ndarray, rgb: np.ndarray, alpha: np.ndarray, mask: np.ndarray) -> None:
    """Alpha-composite an emblem layer, clipped to a panel mask."""
    a = (alpha * mask)[..., None]
    canvas *= 1 - a
    canvas += rgb * a
