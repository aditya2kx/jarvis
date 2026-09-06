# skills/tesla_wraps

Generates custom Paint Shop wrap textures for a Tesla, targeting the UV
templates published in [`teslamotors/custom-wraps`](https://github.com/teslamotors/custom-wraps).

Two designs ship today, both rendered for the **2025+ Model Y Premium**
(`modely-2025-premium`) template:

| Design | File | Look |
|---|---|---|
| Dark Knight | [`wraps/Dark_Knight.png`](wraps/Dark_Knight.png) | Tumbler-inspired matte black: hard-edged armour facets a few percent apart in value, one raking blade across the doors, bat crest on the hood and rear hatch |
| Iron Man | [`wraps/Iron_Man.png`](wraps/Iron_Man.png) | Mark-43 livery: candy hot-rod red over champagne gold, plated helmet on the hood, lit eye slits across the nose, chest reactor ringed by a JARVIS target lock on each front door, repulsors at the quarters and tail |

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

### On the Iron Man finish

Three references, one per zone of the car, each on the panel that can actually
carry it:

* **Hood — the helmet.** It is the only panel tall enough to hold one at real
  proportions (`emblems.FACE_ASPECT` = 1.4832 — the helmet is half again as
  tall as it is wide). What makes it read as *the* helmet is not the silhouette
  but the **plate break-up**, so the shapes are traced the same way as the bat:
  segment a flat front-elevation reference into its shell / plate / emitter
  regions, contour each one, simplify, keep the right half, mirror. That yields
  a brow plate with a V notch driven into it from the crown, a cheek plate
  wrapping under both eyes, a chin plate and a crescent jaw plate either side —
  and the red showing between them *is* the eye sockets and the mouth, so
  neither has to be drawn. Shading follows the same principle: each plate is
  domed away from its own outline and bevelled bright along whichever edge
  faces the crown, rather than lit by hand-placed highlights. Width is capped
  at 130px because the hood island forks into two horns below y=310 for the
  windscreen scuttle; a helmet sized to the bounding box gets its crown sliced
  open by that notch.
* **Front doors — the reactor and JARVIS.** The reactor is the light source and
  the reticle is what it projects, so they belong on the same panel, and the
  reticle is composited *over* the reactor so the bloom falls behind the
  hairlines. The grammar is JARVIS's: rings broken into segments
  (`paint.dashes()`), a frame of four square corner brackets, two heavy gauge
  arcs, and a tick ladder. Unbroken concentric circles read as a clock face.
  Every stroke is specified in **pixels**, not fractions of the radius, because
  the film HUD reads as projected light precisely by never gaining weight —
  though note `paint.ring()` feathers both edges, so a stroke under ~2.4px just
  fades rather than sharpening. `paint.ticks()` measures perpendicular distance
  to each tick's ray for the same reason: thresholding on angle makes ticks
  thinner than a pixel at large radii and they render dashed.
* **Nose and tail — the eye slits and the boot thruster.** The front fascia is
  the car's face, so it wears the helmet's own eye, lifted out of the faceplate
  and rescaled (`emblems.slit_outline()`, `emblems.SLIT_ASPECT`) so the nose and
  the hood can never drift apart.

The palette is champagne gold over deep candy red, not brass over orange. Panel
radii for the reactors are bounded by each panel's *inscribed* circle rather
than its bounding box, since the reactor's bloom reaches 1.30x its radius and
the reticle's brackets reach 1.13x. Each reactor also carries a `rotate`/`flip`
pair, because only its core triangle has an orientation and the atlas points
"up the car" a different way in every zone — stamped flat, a door or tail
reactor ends up apex-down.

The armour and the helmet are Marvel trademarks; same personal-use caveat as
the bat crest above.

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
  rocker — towards larger X on the left of the car and smaller X on the right.
  That is why `emblems.stamp()` takes a `rotate` argument: a badge on the door
  has to be turned 90° to stand upright on the car (-90 on the left flank, 90
  on the right).

Each island exposes a `frame()` of `(u, v)` grids. `u` is global and
longitudinal; `v` is normalised per panel, which is what keeps a beltline
stripe on the beltline as it crosses the fender, both doors and the quarter
panel even though those islands are differently shaped and not axis-aligned.

The large transparent region in the middle of the atlas is the panoramic glass
roof. It is not part of the wrap and cannot be painted.

Rendered output leaves the gutters filled rather than transparent
(`Atlas.close_seams`), so texture filtering at island edges cannot pull white
fringing onto the bodywork.
