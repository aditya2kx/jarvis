"""CLI: render wrap PNGs ready to drop on a USB stick or upload from the app.

    python3 -m skills.tesla_wraps.generate --variant modely-2025-premium

Tesla's constraints (see teslamotors/custom-wraps): PNG, 512-1024px square,
under 1 MB, filename limited to 30 alphanumeric/underscore/dash/space chars.
``--check`` re-validates already-generated files against those rules.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np
from PIL import Image

from .atlas import load_template
from .designs import DESIGNS

OUTPUT_DIR = Path(__file__).parent / "wraps"
MAX_BYTES = 1_000_000
NAME_RE = re.compile(r"^[A-Za-z0-9_\- ]{1,30}$")


def validate(path: Path) -> None:
    """Raise if a rendered wrap would be rejected by the car."""
    stem = path.stem
    if not NAME_RE.match(stem):
        raise ValueError(f"{path.name}: name must be <=30 alphanumeric/_/-/space characters")
    size = path.stat().st_size
    if size > MAX_BYTES:
        raise ValueError(f"{path.name}: {size} bytes exceeds the 1 MB limit")
    with Image.open(path) as im:
        if im.format != "PNG":
            raise ValueError(f"{path.name}: must be PNG, got {im.format}")
        if im.size[0] != im.size[1] or not 512 <= im.size[0] <= 1024:
            raise ValueError(f"{path.name}: must be square between 512 and 1024px, got {im.size}")


def render(name: str, variant: str, out_dir: Path) -> Path:
    atlas = load_template(variant)
    canvas = DESIGNS[name](atlas)
    rgb = np.rint(np.clip(canvas.rgb, 0, 1) * 255).astype(np.uint8)
    alpha = np.rint(np.clip(canvas.alpha, 0, 255)).astype(np.uint8)
    rgb, alpha = atlas.close_seams(rgb, alpha)

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.png"
    Image.fromarray(np.dstack([rgb, alpha]), mode="RGBA").save(path, optimize=True)
    validate(path)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", default="modely-2025-premium")
    parser.add_argument("--design", action="append", choices=sorted(DESIGNS))
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--check", action="store_true", help="validate existing files only")
    args = parser.parse_args(argv)

    names = args.design or sorted(DESIGNS)
    if args.check:
        for name in names:
            path = args.out / f"{name}.png"
            validate(path)
            print(f"ok   {path} ({path.stat().st_size / 1024:.0f} KB)")
        return 0

    for name in names:
        path = render(name, args.variant, args.out)
        print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
