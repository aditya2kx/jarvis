"""Painting primitives for wrap textures.

Everything works on a float RGB canvas of shape ``(h, w, 3)`` in 0..1 so that
gradients and blends compose without banding; the canvas is quantised to 8-bit
only when the PNG is written.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage


def hex_rgb(value: str) -> np.ndarray:
    value = value.lstrip("#")
    return np.array([int(value[i : i + 2], 16) for i in (0, 2, 4)], dtype=np.float64) / 255.0


def fill(canvas: np.ndarray, mask: np.ndarray, colour: str | np.ndarray) -> None:
    canvas[mask] = hex_rgb(colour) if isinstance(colour, str) else colour


def ramp(t: np.ndarray, stops: list[tuple[float, str]]) -> np.ndarray:
    """Sample a multi-stop colour ramp at every point of ``t`` (0..1)."""
    positions = np.array([p for p, _ in stops])
    colours = np.stack([hex_rgb(c) for _, c in stops])
    tc = np.clip(t, positions[0], positions[-1])
    out = np.empty(t.shape + (3,))
    for channel in range(3):
        out[..., channel] = np.interp(tc, positions, colours[:, channel])
    return out


def gradient(
    canvas: np.ndarray, mask: np.ndarray, t: np.ndarray, stops: list[tuple[float, str]]
) -> None:
    canvas[mask] = ramp(t, stops)[mask]


def band(t: np.ndarray, lo: float, hi: float, feather: float = 0.01) -> np.ndarray:
    """Soft-edged 0..1 selector for ``lo <= t <= hi``."""
    return np.clip((t - lo) / feather, 0, 1) * np.clip((hi - t) / feather, 0, 1)


def blend(canvas: np.ndarray, colour: str | np.ndarray, weight: np.ndarray) -> None:
    """Composite a flat colour over the canvas with a per-pixel weight."""
    rgb = hex_rgb(colour) if isinstance(colour, str) else colour
    w = weight[..., None]
    canvas *= 1 - w
    canvas += rgb * w


def shade(canvas: np.ndarray, amount: np.ndarray) -> None:
    """Multiply/lift luminance. ``amount`` > 0 lightens, < 0 darkens."""
    a = amount[..., None]
    np.clip(canvas * (1 + a) + np.clip(a, 0, None) * 0.12, 0, 1, out=canvas)


def fractal_noise(shape: tuple[int, int], seed: int, octaves: int = 5, base: float = 64.0):
    """Smooth multi-octave value noise in 0..1, used for metal grain and grunge."""
    rng = np.random.default_rng(seed)
    total = np.zeros(shape)
    amplitude, sigma = 1.0, base
    for _ in range(octaves):
        layer = ndimage.gaussian_filter(rng.random(shape), sigma=sigma, mode="wrap")
        layer -= layer.min()
        layer /= max(layer.max(), 1e-9)
        total += amplitude * layer
        amplitude *= 0.5
        sigma = max(sigma / 2.2, 0.8)
    total -= total.min()
    return total / max(total.max(), 1e-9)


def carbon_weave(shape: tuple[int, int], pitch: int = 6) -> np.ndarray:
    """Fine twill pattern in -1..1, for a woven carbon-fibre sheen."""
    ys, xs = np.mgrid[0 : shape[0], 0 : shape[1]]
    a = np.sin(2 * np.pi * xs / pitch) * np.sin(2 * np.pi * ys / (pitch * 2))
    b = np.sin(2 * np.pi * (xs + ys) / (pitch * 3))
    return np.clip(0.7 * a + 0.3 * b, -1, 1)


def brushed(shape: tuple[int, int], seed: int, length: float = 26.0) -> np.ndarray:
    """Directional streaks in -1..1, for brushed/anodised metal."""
    rng = np.random.default_rng(seed)
    streaks = ndimage.gaussian_filter1d(rng.standard_normal(shape), sigma=length, axis=0)
    streaks = ndimage.gaussian_filter1d(streaks, sigma=0.6, axis=1)
    streaks /= max(np.abs(streaks).max(), 1e-9)
    return streaks


def edge_distance(mask: np.ndarray) -> np.ndarray:
    """Distance in pixels from each masked pixel to the island edge."""
    return ndimage.distance_transform_edt(mask)


def radial(shape: tuple[int, int], cx: float, cy: float, radius: float) -> np.ndarray:
    ys, xs = np.mgrid[0 : shape[0], 0 : shape[1]]
    return np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2) / radius


def diagonal(shape: tuple[int, int], angle_deg: float, phase: float = 0.0) -> np.ndarray:
    """Projection onto a direction, normalised so the canvas spans roughly 0..1."""
    ys, xs = np.mgrid[0 : shape[0], 0 : shape[1]]
    rad = np.radians(angle_deg)
    proj = xs * np.cos(rad) + ys * np.sin(rad)
    proj -= proj.min()
    return proj / max(proj.max(), 1e-9) + phase


def to_image_arrays(canvas: np.ndarray, alpha: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    rgb = np.rint(np.clip(canvas, 0, 1) * 255).astype(np.uint8)
    return rgb, alpha.astype(np.uint8)
