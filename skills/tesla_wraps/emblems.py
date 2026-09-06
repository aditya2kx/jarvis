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


# Right half of a Mark-43 faceplate, walked from the chin up the jaw to the
# cheek corner and on round the crown. Two proportions do the work: the widest
# point is the cheek at 45% of the height (not the temple), and the crown
# tapers hard above it. Get either wrong and the silhouette turns into an egg.
FACE_ASPECT = 1.22

_FACE_RIGHT = [
    (0.000, 0.000), (0.098, 0.003), (0.172, 0.018), (0.234, 0.050),
    (0.288, 0.100), (0.336, 0.164), (0.386, 0.244), (0.436, 0.322),
    (0.478, 0.392), (0.500, 0.448), (0.498, 0.512), (0.488, 0.574),
    (0.470, 0.634), (0.446, 0.692), (0.416, 0.748), (0.380, 0.802),
    (0.338, 0.852), (0.290, 0.898), (0.236, 0.938), (0.176, 0.970),
    (0.108, 0.991), (0.038, 1.000), (0.000, 1.000),
]

# Eye slit, right side: a slanted bar that lifts and widens towards the temple.
_EYE_RIGHT = [(0.078, 0.536), (0.400, 0.576), (0.424, 0.656), (0.080, 0.606)]

# Socket the slit sits in — a shade larger all round, so the light reads as
# recessed behind the plate rather than painted onto it.
_SOCKET_RIGHT = [(0.060, 0.518), (0.410, 0.560), (0.438, 0.674), (0.062, 0.624)]

# Seam where the faceplate meets the skullcap, and the jaw plate's lower edge.
_TEMPLE_SEAM = [(0.442, 0.566), (0.464, 0.664), (0.448, 0.762), (0.398, 0.844), (0.318, 0.906)]
_JAW_SEAM = [(-0.196, 0.074), (-0.100, 0.050), (0.000, 0.043), (0.100, 0.050), (0.196, 0.074)]

# Mouth grille: wide shallow trapezoid low on the face, with vertical slats.
_MOUTH = [(-0.242, 0.290), (0.242, 0.290), (0.210, 0.150), (-0.210, 0.150)]

# A standalone eye slit in its own normalised box, x running inner (-0.5) to
# outer (0.5). Rises and deepens towards the outer end, same as the helmet's.
_SLIT = [(-0.50, 0.16), (0.44, 0.44), (0.50, 0.92), (-0.50, 0.56)]


def slit_outline(mirror: bool = False) -> list[tuple[float, float]]:
    """Closed eye-slit outline; ``mirror`` flips it for the car's other side."""
    return [(-x, y) for x, y in _SLIT] if mirror else list(_SLIT)


def face_outline() -> list[tuple[float, float]]:
    """Closed faceplate silhouette, normalised to x in -0.5..0.5, y in 0..1."""
    return list(_FACE_RIGHT) + [(-x, y) for x, y in reversed(_FACE_RIGHT)]


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
    rgb += paint.hex_rgb("#3E93AB") * (bloom**2)[..., None] * 0.9
    alpha = np.maximum(alpha, bloom**2 * 0.85)
    return np.clip(rgb, 0, 1), np.clip(alpha, 0, 1)


def _blob(nx, ny, x0: float, y0: float, rx: float, ry: float) -> np.ndarray:
    """Soft elliptical falloff, used to model raised forms on the faceplate."""
    return np.exp(-(((nx - x0) / rx) ** 2 + ((ny - y0) / ry) ** 2))


def _stroke(points: list[tuple[float, float]], width: float) -> list[tuple[float, float]]:
    """Expand a polyline into a closed polygon of constant normalised width."""
    left, right = [], []
    for i, (x, y) in enumerate(points):
        ax, ay = points[max(i - 1, 0)]
        bx, by = points[min(i + 1, len(points) - 1)]
        dx, dy = bx - ax, by - ay
        norm = np.hypot(dx, dy) or 1.0
        ox, oy = -dy / norm * width / 2, dx / norm * width / 2
        left.append((x + ox, y + oy))
        right.append((x - ox, y - oy))
    return left + right[::-1]


def faceplate(
    shape: tuple[int, int], cx: float, cy: float, width: float, flip: bool = False
) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(rgb, alpha)`` layers for a machined gold Mark-43 faceplate.

    The gold is shaded by *form*, not by height: highlights sit on the brow,
    the two cheekbones, the nose bridge and the chin, and everything between
    them falls into shadow. Ramping the colour top-to-bottom instead is what
    makes a helmet look like a printed sticker of one.
    """
    height = width * FACE_ASPECT
    ys, xs = np.mgrid[0 : shape[0], 0 : shape[1]]
    ny = (ys - cy) / height + 0.5
    if flip:
        ny = 1.0 - ny
    nx = (xs - cx) / width

    def poly(points: list[tuple[float, float]]) -> np.ndarray:
        return stamp(shape, points, cx, cy, width, height, flip=flip)

    def mirrored(points: list[tuple[float, float]]) -> np.ndarray:
        return np.clip(poly(points) + poly([(-x, y) for x, y in points]), 0, 1)

    shell = poly(face_outline())
    inside = shell > 0.5

    GOLD_HI, GOLD, GOLD_DEEP, GOLD_SHADOW = "#FBEEC0", "#D9B15A", "#96702A", "#4E3714"

    form = (
        1.00 * _blob(nx, ny, 0.000, 0.840, 0.250, 0.135)  # forehead
        + 0.92 * _blob(nx, ny, 0.300, 0.400, 0.140, 0.200)  # cheekbones, running
        + 0.92 * _blob(nx, ny, -0.300, 0.400, 0.140, 0.200)  # down the cheek
        + 0.55 * _blob(nx, ny, 0.215, 0.300, 0.130, 0.150)
        + 0.55 * _blob(nx, ny, -0.215, 0.300, 0.130, 0.150)
        + 0.78 * _blob(nx, ny, 0.000, 0.080, 0.200, 0.080)  # chin
        + 0.62 * _blob(nx, ny, 0.000, 0.670, 0.075, 0.110)  # nose bridge
        + 0.45 * _blob(nx, ny, 0.000, 0.380, 0.045, 0.210)  # centre spine
        + 0.58 * _blob(nx, ny, 0.225, 0.696, 0.170, 0.048)  # brow ridge
        + 0.58 * _blob(nx, ny, -0.225, 0.696, 0.170, 0.048)
    )
    rgb = paint.ramp(
        np.clip(form, 0, 1.35) / 1.35,
        [(0.00, GOLD_SHADOW), (0.22, GOLD_DEEP), (0.58, GOLD), (0.86, GOLD_HI), (1.00, "#FFFBEA")],
    )

    # Rolled edge, so the silhouette stays legible against the red instead of
    # dissolving into it.
    lip = np.clip(1.0 - paint.edge_distance(inside) / 4.5, 0, 1) * inside
    rgb *= (1 - 0.60 * lip)[..., None]

    # Where the faceplate meets the skullcap, and the jaw plate's lower edge.
    # Blurred, so they read as a step in the plating rather than a scratch.
    seams = np.clip(mirrored(_stroke(_TEMPLE_SEAM, 0.014)) + 0.6 * poly(_stroke(_JAW_SEAM, 0.011)), 0, 1)
    seams = ndimage.gaussian_filter(seams, sigma=max(width * 0.007, 0.7))
    rgb *= (1 - 0.34 * seams / max(seams.max(), 1e-9))[..., None]

    # Mouth grille: dark slot with vertical slats. Five, not seven — on a hood
    # the plate lands around 150px wide, and finer slats turn to mush.
    mouth = poly(_MOUTH)
    rgb = rgb * (1 - (0.88 * mouth)[..., None]) + paint.hex_rgb("#120F0C") * (0.88 * mouth)[..., None]
    for x0 in (-0.132, -0.066, 0.000, 0.066, 0.132):
        slat = poly([(x0 - 0.013, 0.158), (x0 + 0.013, 0.158), (x0 + 0.012, 0.282), (x0 - 0.012, 0.282)])
        rgb += paint.hex_rgb("#7A6231") * (0.62 * slat * mouth)[..., None]

    # Eyes: recessed socket, hard-edged emitter, and a bloom that spills onto
    # the gold — the spill is what sells them as light instead of paint.
    socket = mirrored(_SOCKET_RIGHT)
    rgb *= (1 - 0.80 * socket)[..., None]
    eyes = mirrored(_EYE_RIGHT)
    glow = ndimage.gaussian_filter(eyes, sigma=max(width * 0.030, 1.0))
    glow /= max(glow.max(), 1e-9)
    rgb += paint.hex_rgb("#4FC8E6") * (0.62 * glow**1.5)[..., None]
    rgb = rgb * (1 - eyes[..., None]) + paint.hex_rgb("#EAFDFF") * eyes[..., None]

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
    """
    layers = [
        # (radius fraction, stroke px, start deg, span deg, intensity)
        (1.000, 1.6, 0.0, 360.0, 0.50),
        (0.845, 1.0, 0.0, 360.0, 0.32),
        (0.755, 1.5, 18.0, 74.0, 0.60),
        (0.755, 1.5, 198.0, 74.0, 0.60),
    ]
    mask = np.zeros(shape)
    for frac, stroke, start, span, level in layers:
        mask = np.maximum(mask, level * paint.ring(shape, cx, cy, radius * frac, stroke, start, span))

    # Bracket marks sitting outside the outer ring, at the diagonals.
    for start in (32.0, 122.0, 212.0, 302.0):
        mask = np.maximum(
            mask, 0.80 * paint.ring(shape, cx, cy, radius * 1.12, 2.6, start, 26.0)
        )

    # Fine ladder plus every-30-degree major ticks.
    mask = np.maximum(mask, 0.45 * paint.ticks(shape, cx, cy, radius * 0.885, radius * 0.965, 60))
    mask = np.maximum(
        mask, 0.75 * paint.ticks(shape, cx, cy, radius * 0.855, radius * 0.985, 12, width=2.2)
    )
    # Crosshair, in the gap between the reactor's bezel and the first ring.
    mask = np.maximum(
        mask, 0.70 * paint.ticks(shape, cx, cy, radius * 0.725, radius * 0.805, 4, width=2.2)
    )
    return np.clip(mask, 0, 1)


def composite(canvas: np.ndarray, rgb: np.ndarray, alpha: np.ndarray, mask: np.ndarray) -> None:
    """Alpha-composite an emblem layer, clipped to a panel mask."""
    a = (alpha * mask)[..., None]
    canvas *= 1 - a
    canvas += rgb * a
