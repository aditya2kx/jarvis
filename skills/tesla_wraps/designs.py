"""The wrap designs themselves.

Each design paints a float RGB canvas panel-by-panel using the atlas frames, so
a rule like "gold fades out of the beltline as you move towards the rear" is
written once and lands correctly on the fender, both doors and the quarter
panel even though those islands are different shapes.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from . import emblems, paint
from .atlas import SIDE_PANELS, Atlas


# Bat crest placements: (panel, centre_y, width, faces_rearward). Centre X is
# the panel's own centroid. These are sized to the panel rather than centred
# blindly: the hood tapers towards the bumper so its crest sits aft of centre,
# and the hatch band is only ~41px of full-width texture, so a wider crest
# there would clip its wingtips against the rear glass.
# `flip` orients hood art for a viewer standing at the nose — verified against
# Tesla's own Reindeer example, whose muzzle points at the bumper and antlers
# at the windscreen.
DARK_KNIGHT_CRESTS = (
    ("hood", 246.0, 244.0, True),
    ("rear_hatch", 900.0, 106.0, False),
)

# Iron Man placements. The faceplate is (panel, centre_y, width, flip); `flip`
# is False so the chin points at the bumper and the crown at the windscreen,
# the same orientation as the bat crest and Tesla's own examples. The width is
# the largest that clears the hood's scuttle notch — the hood island splits
# into two horns below y=310, and a helmet sized to the bounding box instead
# would have its crown sliced open by that V.
IRON_MAN_FACE = ("hood", 210.0, 130.0, False)
# (panel, reactor radius). Radii are bounded by each panel's inscribed circle —
# the reticle reaches 1.61x the reactor radius, so a door reactor above ~46px
# would throw its brackets across the window line.
# (panel, reactor radius, rotate, flip). Only the core triangle has an
# orientation, but it needs one: the atlas points "up the car" a different way
# on every zone, so a reactor stamped flat onto a door or the tail ends up
# apex-down. Same rule as the bat crest — whatever makes the emblem upright.
IRON_MAN_REACTORS = (
    ("front_door_l", 44.0, -90, False),
    ("front_door_r", 44.0, 90, False),
)
# The reactor's bloom reaches 1.30x its radius, so these are sized against the
# panel's inscribed circle, not its bounding box.
IRON_MAN_REPULSORS = (
    ("quarter_panel_l", 21.0, -90, False),
    ("quarter_panel_r", 21.0, 90, False),
    ("rear_fascia", 26.0, 0, True),
)


class Canvas:
    """A float RGB canvas plus its alpha, with per-panel coordinate frames."""

    def __init__(self, atlas: Atlas):
        self.atlas = atlas
        self.shape = atlas.size[::-1]
        self.rgb = np.zeros(self.shape + (3,))
        self.alpha = np.zeros(self.shape)
        self.painted = atlas.painted
        self.alpha[self.painted] = 255.0

        # Longitudinal coordinate is global (front of car -> rear of car).
        ys, _ = np.mgrid[0 : self.shape[0], 0 : self.shape[1]]
        self.u = ys / (self.shape[0] - 1)

        # Transverse coordinate is normalised per panel, which is what keeps a
        # beltline stripe on the beltline across differently shaped islands.
        self.v = np.zeros(self.shape)
        for name in atlas.islands:
            island = atlas[name]
            _, v = island.frame(self.shape)
            self.v[island.mask] = v[island.mask]

        self.body = atlas.mask_of(*SIDE_PANELS)
        self.seam = self._seam_proximity()

    def _seam_proximity(self) -> np.ndarray:
        """1.0 at a panel's edge falling to 0 a few pixels in — for panel-gap shading."""
        depth = paint.edge_distance(self.painted)
        return np.clip(1.0 - depth / 4.0, 0, 1) * self.painted

    def mask(self, *names: str) -> np.ndarray:
        return self.atlas.mask_of(*names)

    def centre_of(self, name: str) -> tuple[float, float]:
        return self.atlas[name].centroid


def _metal_grain(canvas: Canvas, seed: int, grain: float, weave: float) -> None:
    """Break up flat fills with brushed streaks and a woven micro-texture."""
    texture = grain * paint.brushed(canvas.shape, seed)
    texture += weave * paint.carbon_weave(canvas.shape)
    texture += 0.05 * (paint.fractal_noise(canvas.shape, seed + 1, base=40.0) - 0.5)
    paint.shade(canvas.rgb, texture * canvas.painted)


def dark_knight(atlas: Atlas) -> Canvas:
    """Tumbler-inspired matte black: flat armour facets, no shine, bat crest.

    The Tumbler's finish is bead-blasted matte over hard angular plating, so
    this design deliberately has no accent colour, no gradient that could read
    as a highlight, and no metallic grain — every bit of interest comes from
    hard-edged facets sitting a few percent apart in value.
    """
    c = Canvas(atlas)
    rgb, u, v = c.rgb, c.u, c.v

    VOID, BLACK, CHARCOAL, GRAPHITE, PLATE = (
        "#08090B",
        "#101215",
        "#191C20",
        "#23272C",
        "#2E3238",
    )

    paint.fill(rgb, c.painted, BLACK)

    # Armour facets. The boundaries are straight lines in (u, v), so the steps
    # between them stay angular instead of blending into a soft gradient.
    body = c.body
    shoulder = 0.16 - 0.05 * u
    waist = 0.56 + 0.10 * u
    sill = 0.82 + 0.03 * u
    paint.fill(rgb, body & (v < shoulder), PLATE)
    paint.fill(rgb, body & (v >= shoulder) & (v < waist), GRAPHITE)
    paint.fill(rgb, body & (v >= waist) & (v < sill), CHARCOAL)
    paint.fill(rgb, body & (v >= sill), VOID)

    # The blade: one long wedge raking down across the doors as it runs
    # rearward, which is the Tumbler's defining line.
    rake = 0.36 + 0.62 * u
    paint.shade(rgb, 0.15 * (body & (v > rake - 0.13) & (v < rake)))
    paint.shade(rgb, -0.10 * u * body)

    # Trim stays flat black — the Tumbler has no bright work anywhere.
    paint.fill(rgb, c.mask("roof_rail_l", "roof_rail_r", "rail_rear_l", "rail_rear_r"), VOID)
    paint.fill(rgb, c.mask("mirror_l", "mirror_r"), CHARCOAL)

    fascia = c.mask("front_fascia")
    paint.gradient(rgb, fascia, u, [(0.005, CHARCOAL), (0.055, BLACK), (0.115, VOID)])

    rear = c.mask("rear_fascia", "rear_corner_l", "rear_corner_r")
    paint.gradient(rgb, rear, u, [(0.85, BLACK), (1.0, VOID)])

    hood = c.mask("hood")
    paint.gradient(rgb, hood, u, [(0.107, CHARCOAL), (0.33, PLATE)])
    hatch = c.mask("rear_hatch")
    paint.fill(rgb, hatch, GRAPHITE)

    paint.shade(rgb, paint.matte_grain(c.shape, seed=11) * c.painted)

    for panel, cy, width, flip in DARK_KNIGHT_CRESTS:
        cx, _ = c.centre_of(panel)
        _stamp_bat(c, c.mask(panel), cx, cy, width=width, colour=VOID, flip=flip)

    # Panel gaps, kept shallow so they read as shut lines rather than outlines.
    paint.shade(rgb, -0.30 * c.seam)
    return c


def _stamp_bat(
    c: Canvas,
    mask,
    cx: float,
    cy: float,
    width: float,
    colour: str,
    flip: bool = False,
    rotate: int = 0,
):
    """Stamp the bat at its true aspect ratio — never stretched to fit a panel."""
    bat = emblems.stamp(
        c.shape,
        emblems.bat_outline(),
        cx,
        cy,
        width,
        width * emblems.BAT_ASPECT,
        flip=flip,
        rotate=rotate,
    )
    paint.blend(c.rgb, colour, bat * mask)


def iron_man(atlas: Atlas) -> Canvas:
    """Mark-43 livery: candy hot-rod red, champagne gold, JARVIS in cyan.

    Three references, one per zone of the car. The hood carries the faceplate,
    because it is the only panel with the height to hold a helmet at its real
    proportions. The front doors carry a chest reactor ringed by a JARVIS
    targeting reticle — the reactor is the light source and the HUD is what it
    projects, so they belong on the same panel. The nose and tail carry the
    eye slits and the boot thruster.

    The screen suits are champagne gold over deep candy red, not brass over
    orange, and the HUD is always a hairline. Both are easy to overshoot and
    both are what separates this from a costume.
    """
    c = Canvas(atlas)
    rgb, u, v = c.rgb, c.u, c.v

    RED_HI, RED, RED_DEEP, RED_SHADOW = "#D22B30", "#A0151B", "#5E0B11", "#2A060A"
    GOLD_HI, GOLD, GOLD_DEEP, GOLD_SHADOW = "#F6E2AC", "#D8B563", "#95712C", "#4A3616"
    GUNMETAL = "#15161A"
    HUD = "#8CEBFF"

    paint.fill(rgb, c.painted, RED)

    # Flanks: candy red rolling off into a gunmetal rocker, the way the Mark's
    # thigh plating sits over dark under-armour.
    body = c.body
    paint.gradient(
        rgb,
        body,
        v,
        [
            (0.00, RED_HI),
            (0.30, RED),
            (0.66, RED_DEEP),
            (0.84, RED_SHADOW),
            (0.92, GUNMETAL),
            (1.00, "#0C0D10"),
        ],
    )

    # Gold pauldron: a hard-edged plate over the front wheel that tapers away
    # towards the rear, with a bright machined rim along its lower edge. A soft
    # fade here would read as an airbrush; the suit's plates have real edges.
    shoulder = np.clip(0.46 - 0.70 * (u - 0.11), 0.0, 1.0)
    plate = np.clip((shoulder - v) / 0.010, 0, 1) * body
    across = np.clip(v / np.maximum(shoulder, 1e-6), 0, 1)
    gold = paint.ramp(across, [(0.0, GOLD_DEEP), (0.35, GOLD_HI), (0.72, GOLD), (1.0, GOLD_DEEP)])
    rgb *= 1 - plate[..., None]
    rgb += gold * plate[..., None]
    paint.blend(rgb, GOLD_HI, 0.55 * paint.band(shoulder - v, 0.0, 0.018, 0.005) * body)

    # Hip flash kicking back up over the rear quarter.
    quarter = c.mask("quarter_panel_l", "quarter_panel_r")
    flash = paint.band(v - (1.02 - 1.05 * u), -0.10, 0.015, 0.020) * quarter
    paint.blend(rgb, GOLD, 0.80 * flash)
    paint.blend(rgb, GOLD_HI, 0.45 * paint.band(v - (1.02 - 1.05 * u), -0.10, -0.082, 0.008) * quarter)

    # Armour seams. Longitudinal splits rake with the beltline; transverse ones
    # sit mid-panel so they read as plate joins rather than doubling the shut
    # lines the panel gaps already draw. Both are masked out of the gold, where
    # they would read as scratches in the plating rather than joins between it.
    red = body & (plate < 0.5)
    for offset, rake in ((0.30, 0.30), (0.55, 0.20), (0.76, 0.10)):
        edge = v - (offset + rake * u)
        paint.blend(rgb, GOLD_SHADOW, 0.42 * paint.band(edge, -0.005, 0.005, 0.003) * red)
        paint.blend(rgb, GOLD_DEEP, 0.26 * paint.band(edge, 0.005, 0.011, 0.003) * red)
    for at in (0.46, 0.67, 0.85):
        paint.blend(rgb, GOLD_SHADOW, 0.26 * paint.band(u, at - 0.004, at + 0.004, 0.002) * red)

    trim = c.mask("roof_rail_l", "roof_rail_r", "rail_rear_l", "rail_rear_r")
    paint.gradient(rgb, trim, u, [(0.3, "#26272C"), (0.95, GUNMETAL)])
    paint.gradient(rgb, c.mask("mirror_l", "mirror_r"), v, [(0.0, GOLD_HI), (1.0, GOLD_DEEP)])

    # Nose: gold mask panel with the helmet's eye slits lit across it.
    fascia = c.mask("front_fascia")
    lateral = np.abs(np.mgrid[0 : c.shape[0], 0 : c.shape[1]][1] - 512) / 266.0
    paint.gradient(
        rgb,
        fascia,
        lateral,
        [
            (0.00, GOLD),
            (0.14, GOLD_HI),
            (0.32, GOLD),
            (0.46, GOLD_DEEP),
            (0.54, RED),
            (1.00, RED_DEEP),
        ],
    )
    paint.blend(rgb, GUNMETAL, 0.9 * paint.band(u, 0.0, 0.020, 0.005) * fascia)
    for cx, mirror in ((435.0, True), (589.0, False)):
        outline = emblems.slit_outline(mirror)
        # Sized by the helmet's own eye aspect so the nose and the hood agree.
        socket = emblems.stamp(c.shape, outline, cx, 62.0, 116.0, 116.0 * emblems.SLIT_ASPECT)
        slit = emblems.stamp(c.shape, outline, cx, 62.0, 104.0, 104.0 * emblems.SLIT_ASPECT)
        socket, slit = socket * fascia, slit * fascia
        paint.blend(rgb, "#0B1216", 0.88 * socket)
        paint.blend(rgb, HUD, 0.50 * ndimage.gaussian_filter(slit, sigma=5.0) * fascia)
        paint.blend(rgb, "#DEF8FF", 0.94 * slit)

    # Hood: red field for the faceplate to sit on, gold only as an edge rib.
    hood = c.mask("hood")
    paint.gradient(
        rgb,
        hood,
        v,
        [(0.0, RED_HI), (0.46, RED), (0.84, RED_DEEP), (0.93, GOLD_DEEP), (1.0, GOLD)],
    )

    rear = c.mask("rear_hatch", "rear_fascia", "rear_corner_l", "rear_corner_r")
    paint.gradient(rgb, rear, u, [(0.85, RED_HI), (0.93, RED), (0.97, RED_DEEP), (1.0, RED_SHADOW)])
    # Tailgate: a machined gold rib with a JARVIS hairline running through it.
    hatch = c.mask("rear_hatch")
    paint.gradient(
        rgb,
        hatch,
        u,
        [
            (0.853, GOLD_SHADOW),
            (0.858, GOLD_DEEP),
            (0.868, GOLD),
            (0.877, GOLD_HI),
            (0.886, GOLD),
            (0.899, GOLD_DEEP),
            (0.910, GOLD_SHADOW),
        ],
    )
    paint.blend(rgb, HUD, 0.55 * paint.band(u, 0.8805, 0.8825, 0.0008) * hatch)

    _metal_grain(c, seed=29, grain=0.020, weave=0.010)
    # Metallic flake, so the red reads as candy paint rather than flat vinyl.
    paint.shade(rgb, 0.09 * (paint.fractal_noise(c.shape, 71, octaves=2, base=1.6) - 0.5) * body)
    sheen = np.clip(1.0 - np.abs(paint.diagonal(c.shape, 58.0) - 0.42) / 0.20, 0, 1) ** 2
    paint.shade(rgb, 0.14 * sheen * c.painted)

    # JARVIS: chest reactor plus its projected reticle, one per front door.
    for panel, radius, rotate, flip in IRON_MAN_REACTORS:
        px, py = c.centre_of(panel)
        panel_mask = c.mask(panel)
        layer_rgb, layer_a = emblems.arc_reactor(c.shape, px, py, radius, flip, rotate)
        emblems.composite(rgb, layer_rgb, layer_a, panel_mask)
        # Drawn over the reactor, not under it: the reticle is what the reactor
        # projects, so its bloom should fall behind the hairlines.
        reticle = emblems.hud(c.shape, px, py, radius * 1.42) * panel_mask
        paint.shade(rgb, 0.9 * ndimage.gaussian_filter(reticle, sigma=2.6) * panel_mask)
        paint.blend(rgb, HUD, 0.72 * reticle)

    # Palm repulsors on the rear quarters, and the boot thruster at the tail.
    for panel, radius, rotate, flip in IRON_MAN_REPULSORS:
        px, py = c.centre_of(panel)
        panel_mask = c.mask(panel)
        layer_rgb, layer_a = emblems.arc_reactor(c.shape, px, py, radius, flip, rotate)
        emblems.composite(rgb, layer_rgb, layer_a, panel_mask)

    panel, cy, width, flip = IRON_MAN_FACE
    fx, _ = c.centre_of(panel)
    hood_mask = c.mask(panel)
    layer_rgb, layer_a = emblems.faceplate(c.shape, fx, cy, width, flip=flip)
    # Contact shadow, without which the helmet's dark crown disappears into the
    # dark end of the hood gradient.
    halo = ndimage.gaussian_filter(layer_a, sigma=5.0) - layer_a
    paint.shade(rgb, -0.85 * np.clip(halo, 0, 1) * hood_mask)
    emblems.composite(rgb, layer_rgb, layer_a, hood_mask)

    paint.shade(rgb, -0.40 * c.seam)
    return c


DESIGNS = {"Dark_Knight": dark_knight, "Iron_Man": iron_man}
