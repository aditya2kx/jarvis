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
        return np.clip(1.0 - depth / 7.0, 0, 1) * self.painted

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
    """Matte black armour with gunmetal facets, amber hairlines and a bat crest."""
    c = Canvas(atlas)
    rgb, u, v = c.rgb, c.u, c.v

    INK, GRAPHITE, GUNMETAL = "#05070A", "#161C25", "#2A323E"
    AMBER, STEEL = "#E8B23A", "#3D4959"

    paint.fill(rgb, c.painted, INK)

    # Body sides: lit along the beltline, falling into black at the rocker.
    body = c.body
    paint.gradient(
        rgb, body, v, [(0.0, "#333F51"), (0.28, "#1B2330"), (0.70, "#080B10"), (1.0, "#020305")]
    )
    paint.shade(rgb, -0.18 * u * body)

    # The armour break: a shoulder plate whose lower edge steps down once as it
    # runs rearward, so the black-on-black split has an angular kink rather than
    # reading as a plain pinstripe.
    sweep = 0.30 + 0.30 * u + 0.16 * np.clip((u - 0.52) / 0.06, 0, 1)
    paint.shade(rgb, 0.34 * np.clip((sweep - v) / 0.03, 0, 1) * body)
    paint.blend(rgb, AMBER, 0.75 * paint.band(v - sweep, -0.010, 0.010, 0.005) * body)

    # Faint angular shards for a plated look.
    shards = np.abs(((paint.diagonal(c.shape, 68.0) * 11.0) % 1.0) - 0.5) * 2.0
    paint.shade(rgb, 0.075 * (shards - 0.5) * body)

    # Trim and glass-adjacent parts stay a shade lighter so the car reads as
    # black-on-black rather than a single flat slab.
    trim = c.mask("roof_rail_l", "roof_rail_r", "rail_rear_l", "rail_rear_r", "mirror_l", "mirror_r")
    paint.fill(rgb, trim, GUNMETAL)
    paint.gradient(rgb, trim, u, [(0.3, STEEL), (0.95, GRAPHITE)])

    # Front fascia: graphite across the top edge, amber hairline underneath.
    fascia = c.mask("front_fascia")
    paint.gradient(rgb, fascia, u, [(0.005, "#1B222C"), (0.06, INK), (0.12, "#0A0D12")])
    paint.blend(rgb, AMBER, 0.55 * paint.band(u, 0.088, 0.094, 0.004) * fascia)

    # Rear: hairline above the plate, mirrored on the lower fascia.
    rear = c.mask("rear_hatch", "rear_fascia", "rear_corner_l", "rear_corner_r")
    paint.gradient(rgb, rear, u, [(0.85, "#12171F"), (1.0, "#040508")])
    paint.blend(rgb, AMBER, 0.5 * paint.band(u, 0.858, 0.864, 0.004) * rear)

    _metal_grain(c, seed=11, grain=0.055, weave=0.030)

    # Bat crest on the hood, sitting in a bat-signal pool of cold light. The
    # crest sits low on the island because the hood tapers towards the nose.
    hood = c.mask("hood")
    hx, _ = c.centre_of("hood")
    hy = 248.0
    r = paint.radial(c.shape, hx, hy, 178.0)
    paint.blend(rgb, "#7288A5", np.clip(1.0 - r, 0, 1) ** 1.25 * hood)
    paint.blend(rgb, AMBER, 0.18 * paint.band(r, 0.88, 1.0, 0.07) * hood)
    # Hood art is read by someone standing at the nose, so it faces rearward.
    _stamp_bat(c, hood, hx, hy, width=212.0, height=94.0, amber=AMBER, flip=True)

    # Smaller crest on the rear hatch band.
    hatch = c.mask("rear_hatch")
    rx, ry = c.centre_of("rear_hatch")
    _stamp_bat(c, hatch, rx, ry, width=132.0, height=40.0, amber=AMBER)

    # Quarter-panel badges, sitting just under the beltline on each flank.
    for side in ("l", "r"):
        name = f"quarter_panel_{side}"
        x0, _, x1, _ = atlas[name].bbox
        span = x1 - 1 - x0
        bx = (x1 - 1 - 0.36 * span) if side == "l" else (x0 + 0.36 * span)
        _stamp_bat(
            c,
            c.mask(name),
            bx,
            858.0,
            width=112.0,
            height=48.0,
            amber=AMBER,
            rotate=90 if side == "l" else -90,
        )

    # Darken every panel edge so the seams read as real panel gaps.
    paint.shade(rgb, -0.45 * c.seam)
    return c


def _stamp_bat(
    c: Canvas,
    mask,
    cx: float,
    cy: float,
    width: float,
    height: float,
    amber: str,
    flip: bool = False,
    rotate: int = 0,
):
    bat = emblems.stamp(
        c.shape, emblems.bat_outline(), cx, cy, width, height, flip=flip, rotate=rotate
    )
    halo = emblems.outlined(bat, thickness=2.4)
    paint.blend(c.rgb, "#040507", bat * mask)
    paint.blend(c.rgb, amber, 0.9 * halo * mask)


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
