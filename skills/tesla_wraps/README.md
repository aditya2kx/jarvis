# skills/tesla_wraps

Generates custom Paint Shop wrap textures for a Tesla, targeting the UV
templates published in [`teslamotors/custom-wraps`](https://github.com/teslamotors/custom-wraps).

Two designs ship today, both rendered for the **2025+ Model Y Premium**
(`modely-2025-premium`) template:

| Design | File | Look |
|---|---|---|
| Dark Knight | [`wraps/Dark_Knight.png`](wraps/Dark_Knight.png) | Tumbler-inspired matte black: hard-edged armour facets a few percent apart in value, one raking blade across the doors, bat crest on the hood and rear hatch |
| Iron Man | [`wraps/Iron_Man.png`](wraps/Iron_Man.png) | Hot-rod red with gold shoulder plating and a gold spine down the hood, charcoal rocker skirt, arc reactor on the hood and rear fascia |

### On the Dark Knight finish

The Tumbler's paint is bead-blasted matte over angular plating, so that design
carries no accent colour, no metallic grain, and no gradient that could read as
a highlight — a single specular sweep makes the whole thing look like gloss
vinyl instead. All the interest comes from hard facet steps between five close
values of near-black, plus `paint.matte_grain()`, which is deliberately
directionless because any streaking reads as satin.

The crest is the wide, flat Nolan-trilogy silhouette (`emblems.BAT_ASPECT` =
0.3385 — barely a third as tall as it is wide). Getting that ratio wrong is the
fastest way to make it look like fan art, so `_stamp_bat()` always derives
height from width and never stretches the shape to fill a panel. It is a
Warner Bros. trademark; this is a personal-use vehicle wrap, not artwork to
redistribute.

## Getting them onto the car

1. Copy the two PNGs from `wraps/` to a USB drive formatted exFAT / FAT32 /
   ext4 (**not** NTFS), in a root-level folder named `Wraps`. The drive must
   not also hold map or firmware update files.
   Alternatively, upload them from the Tesla mobile app (v4.59.0 or later):
   **Creations → Wrap → Upload**.
2. In the car: **Toybox → Paint Shop → Wraps**.

The car accepts up to 10 wraps from USB and 10 from the app.

## Picking the right template

Every trim unwraps its bodywork differently, and a texture built for one
layout will smear across the wrong panels on another. `modely-2025-premium` is
the template for a 2026 Model Y **Dual Motor** — that is the Long Range AWD
car, which Tesla now sells as the Premium trim. If the car's screen instead
reads *Model Y Performance* or *Model Y Standard*, it needs
`modely-2025-performance` or `modely-2025-base`, whose atlases have 22 and 19
islands respectively; `atlas.py` refuses to run against those rather than
silently mis-mapping panels, so they would each need their own panel
classification first.

## Regenerating

```bash
python3 -m skills.tesla_wraps.generate                 # render both designs
python3 -m skills.tesla_wraps.generate --design Iron_Man
python3 -m skills.tesla_wraps.generate --check         # validate existing files
python3 -m pytest skills/tesla_wraps/
```

The upstream template is fetched on first run and cached in
`.template_cache/` (gitignored — it is Tesla's artwork, not ours).

## How the atlas is read

The template is a 1024x1024 PNG of opaque white islands separated by
transparent gutters, one island per body panel. `atlas.py` labels the 20
islands and names them from geometry alone, which gives designs a vocabulary
(`front_door_l`, `quarter_panel_r`, `rear_hatch`) instead of pixel boxes.

Two facts about the layout drive everything else:

* **Texture Y runs nose-to-tail.** The front fascia sits at the top of the
  image (its headlight and front-camera cutouts are the giveaway) and the rear
  bumper at the bottom, so a front-to-rear gradient is just a gradient in `y`.
* **Texture X means different things in different places.** Across the hood and
  the fascias it runs laterally across the car, but on the flanks it runs
  *vertically up the body* — inner edge at the beltline, outer edge at the
  rocker. That is why `emblems.stamp()` takes a `rotate` argument: a badge on
  the door has to be turned 90° to stand upright on the car.

Each island exposes a `frame()` of `(u, v)` grids. `u` is global and
longitudinal; `v` is normalised per panel, which is what keeps a beltline
stripe on the beltline as it crosses the fender, both doors and the quarter
panel even though those islands are differently shaped and not axis-aligned.

The large transparent region in the middle of the atlas is the panoramic glass
roof. It is not part of the wrap and cannot be painted.

Rendered output leaves the gutters filled rather than transparent
(`Atlas.close_seams`), so texture filtering at island edges cannot pull white
fringing onto the bodywork.
