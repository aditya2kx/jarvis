"""The wrap designs themselves.

Each design paints a float RGB canvas panel-by-panel using the atlas frames, so
a rule like "gold fades out of the beltline as you move towards the rear" is
written once and lands correctly on the fender, both doors and the quarter
panel even though those islands are different shapes.
"""

from __future__ import annotations

import numpy as np

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
    """Hot-rod red over gold armour plating, with arc reactors fore and aft."""
    c = Canvas(atlas)
    rgb, u, v = c.rgb, c.u, c.v

    GOLD, GOLD_HI, GOLD_DEEP = "#C8981F", "#F2CC5B", "#8A6410"
    CHARCOAL = "#17181C"

    paint.fill(rgb, c.painted, "#A8161B")

    # Body sides: red deepening towards the rocker, with a charcoal skirt.
    body = c.body
    paint.gradient(
        rgb,
        body,
        v,
        [(0.0, "#D6262A"), (0.35, "#AE1519"), (0.72, "#6B0C11"), (0.86, "#2A0B0D"), (1.0, CHARCOAL)],
    )

    # Gold shoulder plating: deepest over the front wheel, tapering away as it
    # runs rearward, exactly like the pauldron on a Mark suit.
    shoulder = np.clip(0.42 - 0.62 * (u - 0.11), 0.0, 1.0)
    gold_sel = np.clip((shoulder - v) / 0.018, 0, 1) * body
    rgb[...] = np.where(
        (gold_sel > 0)[..., None],
        paint.ramp(v / np.maximum(shoulder, 1e-6), [(0.0, GOLD_HI), (0.6, GOLD), (1.0, GOLD_DEEP)])
        * gold_sel[..., None]
        + rgb * (1 - gold_sel[..., None]),
        rgb,
    )

    # A second gold flash kicks back up over the rear quarter.
    quarter = c.mask("quarter_panel_l", "quarter_panel_r")
    flash = paint.band(v - (1.05 - 1.1 * u), -0.09, 0.02, 0.02) * quarter
    paint.blend(rgb, GOLD, 0.85 * flash)

    # Thin gold seams tracing the armour plates.
    plate = np.abs(((paint.diagonal(c.shape, 74.0) * 7.0) % 1.0) - 0.5) * 2.0
    paint.blend(rgb, GOLD_HI, 0.18 * np.clip((plate - 0.93) / 0.07, 0, 1) * body)

    trim = c.mask("roof_rail_l", "roof_rail_r", "rail_rear_l", "rail_rear_r")
    paint.gradient(rgb, trim, u, [(0.3, "#2C2D33"), (0.95, CHARCOAL)])
    mirrors = c.mask("mirror_l", "mirror_r")
    paint.gradient(rgb, mirrors, v, [(0.0, GOLD_HI), (1.0, GOLD_DEEP)])

    # Front fascia: gold mask panel with red flanks and a dark intake band.
    fascia = c.mask("front_fascia")
    lateral = np.abs(np.mgrid[0 : c.shape[0], 0 : c.shape[1]][1] - 512) / 266.0
    paint.gradient(
        rgb, fascia, lateral, [(0.0, GOLD_HI), (0.42, GOLD), (0.52, "#B8181C"), (1.0, "#7A0F13")]
    )
    paint.blend(rgb, CHARCOAL, 0.9 * paint.band(u, 0.0, 0.022, 0.006) * fascia)

    # Hood: gold spine down the centre, red shoulders.
    hood = c.mask("hood")
    paint.gradient(
        rgb,
        hood,
        v,
        [(0.0, GOLD_HI), (0.36, GOLD), (0.55, GOLD_DEEP), (0.62, "#B8181C"), (1.0, "#7E1014")],
    )

    rear = c.mask("rear_hatch", "rear_fascia", "rear_corner_l", "rear_corner_r")
    paint.gradient(rgb, rear, u, [(0.85, "#C21E22"), (0.94, "#8E1116"), (1.0, "#360B0E")])
    paint.blend(rgb, GOLD, 0.8 * paint.band(u, 0.856, 0.868, 0.005) * rear)

    _metal_grain(c, seed=29, grain=0.045, weave=0.018)

    # Specular sweep so the red reads as candy paint rather than flat vinyl.
    sheen = np.clip(1.0 - np.abs(paint.diagonal(c.shape, 58.0) - 0.42) / 0.22, 0, 1) ** 2
    paint.shade(rgb, 0.16 * sheen * c.painted)

    hx, _ = c.centre_of("hood")
    layer_rgb, layer_a = emblems.arc_reactor(c.shape, hx, 245.0, 68.0, flip=True)
    emblems.composite(rgb, layer_rgb, layer_a, hood)

    fx, fy = c.centre_of("rear_fascia")
    layer_rgb, layer_a = emblems.arc_reactor(c.shape, fx, fy, 30.0)
    emblems.composite(rgb, layer_rgb, layer_a, c.mask("rear_fascia"))

    paint.shade(rgb, -0.40 * c.seam)
    return c


DESIGNS = {"Dark_Knight": dark_knight, "Iron_Man": iron_man}
