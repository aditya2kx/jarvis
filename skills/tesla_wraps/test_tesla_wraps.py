"""Tests for the wrap generator.

The atlas tests need the upstream template, which is fetched and cached on first
use; they skip when the cache is cold and there is no network.
"""

from __future__ import annotations

import urllib.error

import numpy as np
import pytest

from . import emblems, paint
from .atlas import ALL_PANELS, SIDE_PANELS, load_template
from .designs import DESIGNS
from .generate import NAME_RE, render, validate


@pytest.fixture(scope="module")
def atlas():
    try:
        return load_template()
    except urllib.error.URLError as exc:
        pytest.skip(f"template unavailable offline: {exc}")


# --- atlas ---------------------------------------------------------------


def test_every_panel_is_named(atlas):
    assert set(atlas.islands) == set(ALL_PANELS)


def test_panels_are_left_right_symmetric(atlas):
    """A mirrored pair should sit at mirrored x and near-identical area."""
    for name in ALL_PANELS:
        if not name.endswith("_l"):
            continue
        left, right = atlas[name], atlas[name[:-2] + "_r"]
        assert abs(left.area - right.area) < 0.01 * left.area, name
        assert abs((512 - left.centroid[0]) - (right.centroid[0] - 512)) < 12, name


def test_panels_are_ordered_front_to_rear(atlas):
    """Texture Y must run nose-to-tail, which every design relies on."""
    order = ["front_fascia", "hood", "front_door_l", "rear_door_l", "quarter_panel_l", "rear_fascia"]
    ys = [atlas[name].centroid[1] for name in order]
    assert ys == sorted(ys)


def test_side_panel_frame_puts_beltline_at_v_zero(atlas):
    """On the left flank, v must fall to 0 at the inner (upper) edge."""
    island = atlas["front_door_l"]
    _, v = island.frame(atlas.size[::-1])
    x0, _, x1, _ = island.bbox
    assert v[island.mask].min() < 0.02
    assert v[island.mask].max() > 0.98
    inner = island.mask & (np.mgrid[0:1024, 0:1024][1] > x1 - 12)
    assert v[inner].mean() < 0.12


def test_close_seams_fills_the_gutters(atlas):
    rgb = np.zeros((1024, 1024, 3), np.uint8)
    alpha = np.zeros((1024, 1024), np.uint8)
    rgb[atlas.painted] = 200
    alpha[atlas.painted] = 255
    before = int((alpha > 0).sum())
    _, alpha = atlas.close_seams(rgb, alpha)
    assert int((alpha > 0).sum()) > before


# --- emblems -------------------------------------------------------------


def test_bat_outline_is_normalised_and_symmetric():
    points = emblems.bat_outline()
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    assert min(ys) == pytest.approx(0.0) and max(ys) == pytest.approx(1.0)
    assert min(xs) == pytest.approx(-0.5) and max(xs) == pytest.approx(0.5)


def test_stamp_rotation_swaps_the_emblem_axes():
    """A rotated badge must be tall-and-narrow where the flat one is wide."""
    outline = emblems.bat_outline()
    flat = emblems.stamp((256, 256), outline, 128, 128, 160, 60)
    turned = emblems.stamp((256, 256), outline, 128, 128, 160, 60, rotate=90)
    for mask, wider in ((flat, True), (turned, False)):
        ys, xs = np.nonzero(mask > 0.5)
        assert bool(np.ptp(xs) > np.ptp(ys)) is wider


def test_arc_reactor_is_confined_to_its_radius():
    rgb, alpha = emblems.arc_reactor((256, 256), 128, 128, 40.0)
    assert alpha.shape == (256, 256)
    ys, xs = np.nonzero(alpha > 0.01)
    assert np.sqrt((xs - 128) ** 2 + (ys - 128) ** 2).max() < 40.0 * 1.35
    assert rgb.max() <= 1.0


# --- paint ---------------------------------------------------------------


def test_ramp_interpolates_between_stops():
    t = np.array([0.0, 0.5, 1.0])
    out = paint.ramp(t, [(0.0, "#000000"), (1.0, "#ffffff")])
    assert out[0].tolist() == [0.0, 0.0, 0.0]
    assert out[1] == pytest.approx([0.5, 0.5, 0.5], abs=0.01)


def test_band_selects_the_requested_interval():
    t = np.linspace(0, 1, 101)
    sel = paint.band(t, 0.4, 0.6, feather=0.01)
    assert sel[50] == pytest.approx(1.0)
    assert sel[0] == 0.0 and sel[-1] == 0.0


# --- output contract -----------------------------------------------------


@pytest.mark.parametrize("name", sorted(DESIGNS))
def test_rendered_wrap_meets_tesla_requirements(atlas, name, tmp_path):
    path = render(name, "modely-2025-premium", tmp_path)
    validate(path)  # raises on size/format/name violations

    from PIL import Image

    with Image.open(path) as im:
        arr = np.array(im.convert("RGBA"))
    assert arr.shape == (1024, 1024, 4)
    # Every UV island must be painted; an unpainted panel renders as bare car.
    for panel in SIDE_PANELS:
        assert arr[..., 3][atlas[panel].mask].min() == 255, panel


@pytest.mark.parametrize("stem", ["Dark_Knight", "Iron_Man", "a b-c_1"])
def test_accepted_filenames(stem):
    assert NAME_RE.match(stem)


@pytest.mark.parametrize("stem", ["bad/name", "x" * 31, "emoji✨"])
def test_rejected_filenames(stem):
    assert not NAME_RE.match(stem)
