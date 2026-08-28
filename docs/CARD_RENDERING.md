# Card Rendering

`/render` draws an Azoth card in Python and posts it to Discord — an **animated
GIF** for cards with eigenfunction art, a PNG for the rest. The same command
covers aspects and rites; see [COMMANDS.md](COMMANDS.md#content-lookup).

`show_upgrade:True` renders a side-by-side against the card's upgraded state —
see [Upgrade comparison](#upgrade-comparison) below. Off by default.

Rewritten 2026-08-26. The previous renderer targeted a card template the game had
since replaced.

## What it reproduces, and what it doesn't

It recreates `scenes/cards/card.tscn` from the azoth repo: background, element
border, art, valence, name, subtype and rules text with inline symbols.

**Deliberately not reproduced:**

| Skipped | Why |
|---|---|
| `base_card_shader.gdshader` (1,136 lines) | Tilt, specular, drop shadow — responses to hovering or moving a card in-game. **Its holographic block is now ported** for upgraded cards: `azoth_logic/holo.py`, see [The upgraded card](#the-upgraded-card) |
| Enhancements (`enhancement_visuals.gd`) | Applied during a run; never present on a card as authored |
| Attributes | Same — granted in-game |
| Split-side dimming | `SPLIT_INACTIVE_DIM` fades the side you are not hovering — in-game state with no meaning in a snapshot |


## Holographic sheen

**Every card wears it**, not just upgraded ones. `card.tscn`, `aspect_card.tscn`
and `event_card.tscn` all load `scenes/cards/base_card_material.tres`, where
`_enableHolographic = true` at `_holoIntensity = 0.06`. `azoth_logic/holo.py` is
the port.

⚠️ **Take the constants from the MATERIAL, not the shader's uniform defaults.**
`base_card_shader.gdshader` declares `_brightnessThreshold = 0.15`,
`_saturationWeight = 1.5`, `_metallicness = 0.7`; the material overrides all six
with `0.1`, `2.0` and `3.0`. The first version of this port used the declared
defaults and the sheen was invisible on white faces — which is most of what a
**catalyst** is, since a colourless card gets the white border and white art and
is coloured by nothing else. They rendered flat white in Discord while
shimmering in-game.

`_metallicness = 3.0` is outside `[0, 1]` deliberately: GLSL `mix` extrapolates,
so `mix(tinted, reflection, 3.0)` overshoots hard toward the rainbow. That
overshoot is what lets white pick up colour at an intensity of 0.06. Clamping it
to a normal blend puts the bug back.

**Where it is applied, and where it deliberately is not:**

| Path | Sheen |
|---|---|
| `/render` single face (`render_png`, `render_gif`, `render_aspect`, `render_rite`) | ✅ 0.06 |
| Upgrade comparison, base face | ✅ 0.06 |
| Upgrade comparison, upgraded face | ✅ 0.15 |
| Deck grid, sample hand, `/search` results | ❌ `sheen=False` |

Grids draw at 200px, where the effect cannot be seen, and a 110-card deck would
pay for it once per card to show nothing. `deck_render._still_for` passes
`sheen=False` for that reason, which is also why the comparison applies it
itself — the two faces need different intensities.

**Cost:** ~70ms per face at full card size, so ~4s of a 60-frame loop. Measured
worst case through the real commands, cold: **8.0s for a comparison, 7.1s for a
single animated card**, against a 30s timeout — and cached after.

## The upgraded card

Two things mark the upgraded face, both matching the game.

**A `+` on the name.** `base_card.gd::set_upgrade_card_visuals` appends a single
one and guards against doubling it — `Harbinger` becomes `Harbinger+`. It is a
label, never data: the row that reaches the cache key and the filename is still
the row the database holds.

**A stronger sheen.** Every card is foiled at `0.06`; `set_upgrade_card_visuals`
raises an upgraded one to `0.15`. The difference is the marker — a base card is
not un-foiled. The rainbow itself ports exactly — a spectrum
through three sines at 0, 2pi/3 and 4pi/3, saturation-boosted around a
`dot(colour, vec3(0.633))` grey. So does the `holoAppeal` mask, including the
part that keeps white suppressed: the rules text is white, and shimmering text
is unreadable text.

What could not port is what made the effect exist. In-game the spectrum comes
off a surface normal tilted by where the card sits and where the pointer is;
there is no tilt in a rendered image, so that term is constant and the rainbow
would be frozen. It is replaced by a phase that advances once around the
animation loop — the motion a player gets from tilting, a viewer gets from the
GIF playing. `_fresnelPower` is the same kind of term, and its visible
consequence (stronger toward the edges) is kept as a radial falloff, because a
foil that shimmers hardest in the middle of the card reads as a smudge.

It is subtle in a single frame and much clearer in motion, which is how the
game's own is. `holo.HOLO_INTENSITY` and `holo.UPGRADED_INTENSITY` are the dials.

## Upgrade comparison

`/render` composes a card beside its upgraded state through
`deck_render.render_comparison`, which takes parallel `items` / `kinds` /
`labels`. `kinds` is **required** here, unlike the deck paths that default
everything to `card`: 28 cards upgrade into aspects, and the two sides of a
comparison are routinely different content types.

It reuses `_faces` and `fetch_art_many`, so mixed kinds and parallel art fetch
come for free — the same machinery `/search` uses for a mixed result grid.

**Animated**, unlike the grid and the hand. `_frames_for` mirrors
`card_render.render_gif` and `fate_render.render_aspect` up to but *not*
including `to_gif` — stopping short of the encoder is what makes them
composable. Frames are composed side by side and handed to the same
`card_render.to_gif` every other renderer uses.

A side with one frame holds it while the other moves, so the common
animated-card-into-flat-aspect case still animates on the left. When no side
animates it returns a PNG (16 of 197 cards).

Each side is cropped to its **own** alpha box, computed across all of its frames
at once so the crop cannot jitter mid-animation. A card face carries ~63px of
empty canvas above and below (which `render_png` crops for exactly this reason)
and an aspect face does not, so scaling both to one box without cropping drew the
card visibly smaller than the aspect beside it.

This paragraph used to open "cropping matters more here than in a grid". It does
not — the grid had the identical bug and kept it until 2026-08-28. See
[One tile shape for every kind](#one-tile-shape-for-every-kind).

⚠️ **Only the width is fixed; each face's height follows from its own crop.**
Deriving the height from `CARD_W x CARD_H` — which this did at first — squeezes
the card, because the crop is not that shape: a 552x766 crop (0.72) forced into a
380x609 box (0.624) loses **13% of its width**. The point of cropping is that the
face is no longer the full canvas, so the full canvas cannot supply the target
shape. A card crop and an aspect crop are also different shapes, so the row is as
tall as the tallest face and shorter ones are centred in it.

`COMPARE_GIF_COLORS = 128`, double the single-card 64, because one palette now
covers two colour schemes — an orange card beside a pink aspect. That is the same
budget per side, and costs about 15% on the file.

Measured across 60 comparisons: 0.71 MB largest GIF against Discord's 10 MB cap,
4.5s slowest render against a 30s timeout, instant on a cache hit.

Captions are **ASCII only**. The card font has no arrow glyph and a missing glyph
renders as a silent gap, so `Upgraded -> Aspect` came out as `Upgraded   Aspect`.

## Modules

| File | Role |
|---|---|
| `azoth_logic/card_layout.py` | Geometry and type styling, transcribed from `card.tscn` |
| `azoth_logic/rich_text.py` | Symbol tokens, wrapping, centred layout |
| `azoth_logic/eigenfunction_art.py` | `.exr` art — the port of `split_card_image.gdshader` |
| `azoth_logic/card_render.py` | Composites the face; PNG and GIF output |
| `tools/sync_assets.py` | Refreshes vendored art from a local azoth checkout |

## Two art paths

A card's `image` extension decides everything, mirroring
`ImageCache.eigenfunction_name_for_image()`:

| Extension | Bucket | Output | Count |
|---|---|---|---|
| `.exr` | `eigenfunctions` | Animated GIF | 246 / 400 |
| `.png` | `cardimages` | Static PNG | 154 / 400 |

Wrapping a static PNG in a GIF would be a larger file showing the same thing, so
it isn't done.

### How the animation works

`split_card_image.gdshader` treats the EXR's three colour channels as three
eigenfunction **modes** and superposes them with time-varying weights:

```
Z     = ef.r + w1*ef.g + w2*ef.b
alpha = smoothstep(thr - fwidth(Z), thr + fwidth(Z), abs(Z))
color = secondary if ef.a > 0.75 else primary
```

The alpha channel is a **zone map** (0.5 base / 1.0 accent), not opacity. `w1`
and `w2` come from `organic()`, a phase-modulated sine, scaled by `departure`
(`0.15` from `GlobalVars.EIGENFUNCTION_DEPARTURE`, overridable per card via
`image_data.departure`). At `t = 0` both weights are zero, so the still frame is
just `Z = ef.r`.

**The loop needs a cross-fade.** The shader's frequencies are incommensurate on
purpose — searching 2–60s finds no duration where the motion returns to its start
(the best, at 48.2s, still lands ~30% of the weight range away). The last 25% of
frames blend toward the pre-start frame **in field space**, on `Z`, before
thresholding. Blending rendered pixels instead would cross-dissolve two
hard-edged images and ghost; blending the smooth scalar field slides the edge and
keeps it crisp.

Defaults: **4s at 15fps, cropped to the card, ~283 KB.**

### The card is transparent, and cropped to itself

Output carries real transparency and is cropped to the card's own bounds. Both
matter more than they sound:

- **Transparency.** GIF alpha is 1 bit, so the antialiased rim has to be cut
  somewhere — `ALPHA_CUTOFF = 128`. Measured on a rendered face, that rim is only
  **~3px wide and 98% of the card is fully opaque**, so the midpoint is
  imperceptible. Before this, the card was flattened onto `#313338` — Discord's
  *Dark* theme colour — which on Darker, Midnight or Light read as a grey
  rectangle around the card.
- **The crop.** The 560×897 viewport carried ~63px of empty canvas above and
  below. Discord scaled that down along with the card, so the card arrived
  smaller than it needed to be. A card crops to 552×766, an aspect or rite to
  544×759 — exactly the silhouette measured from Godot.

The crop box is the **union of every frame's** opaque pixels, never per frame:
cropping each frame to its own bounds would make the card jitter as the art moves
inside it. For a card that is currently defensive — the face is identical in
every frame — so it is pinned by a unit test on `alpha_bbox`, not by a render
test, which could not tell the difference.

### One palette for the whole animation

`GIF_COLORS = 64`, shared across every frame rather than adaptive per frame.

Per-frame palettes defeat the GIF optimiser's frame differencing: it can only
encode a changed sub-rectangle when successive frames share a colour table.
Measured on Restoration, 60 frames:

| | Size |
|---|---|
| Per-frame palettes | 806 KB |
| **Shared palette** | **272 KB** |
| Old flattened output, for reference | 453 KB |

So transparency came out **cheaper** than not having it. `disposal=1` ("leave the
previous frame") rather than `2` is part of that — and it is safe only because
**the transparent region is identical in every frame**: the art is composited
onto an opaque card face, so nothing but the outer rim is ever transparent. A
renderer whose transparent area moves would need `disposal=2` or the holes would
ghost.

> **The palette is padded to a full `GIF_COLORS` entries before the transparent
> one is appended.** `getpalette()` returns only the entries actually *used*, so a
> low-colour animation comes back short — a rite background yields four. Appending
> the transparent entry to a short palette puts it at index 4 while the pixel data
> references index 64: a GIF whose pixels point past its own colour table. Pillow
> tolerates reading that back; a stricter decoder need not.

**Rites get `RITE_GIF_COLORS = 16` instead.** A rite's *whole* background changes
each frame, so differencing cannot help it and the palette is its only size
control. Its frames measure ~4,680 distinct colours, but those are all
antialiasing between two hues — checked side by side at 16/32/64 against the
source, they are indistinguishable. 3.0 MB at 64 against **1.7 MB at 16**.

WebP was measured as the alternative: it carries 8-bit alpha and would need no
cutoff, but came out ~20% *larger* than the shared-palette GIF, and GIF is what
was wanted.

## Split cards

11 of 400 cards carry `split: {element, valence}` — a second face. Three things
change, all driven off that one field:

**A second border, blended not stacked.** `card_border_dim.gdshader` merges both
into one draw:

```
coverage = clamp(split.a / max(base.a, 1e-4), 0, 1)
rgb      = mix(base.rgb, split.rgb, coverage)
alpha    = base.a
```

Two details a plain alpha-composite gets wrong, both called out in the shader's
own comments:

- Coverage is the split's alpha **normalised by the base's**, not the split's
  alpha directly. At the antialiased rim both textures fade together, so raw
  alpha lets the base bleed through and the split's edge reads as a soft double
  line. (Measured over the 3,691 rim pixels of the anima/sol pair: normalised
  coverage averages 0.96, raw 0.87 — and the residual against the split colour is
  0.011 vs 0.048.)
- Alpha comes from the **base alone**. If the split contributed alpha it would
  widen the silhouette and the card would gain a second, fatter outline.

**A second valence** in the opposite top corner (`VALENCE2_REL`), hidden by
default in `card.tscn` and shown only by `set_split_card_visuals()`.

**Two-toned art.** From `GlobalVars.get_eigenfunction_colors()`: the accent zone
(alpha 1.0) keeps the card's own element, and the base zone (alpha 0.5) takes the
**split element's** colour instead of white. That is what makes a split card read
as split at a glance.

> **The `split` field is `null` on every non-split card**, not absent — every
> card exported from the database carries it explicitly. A bare key check treats
> all 400 cards as split. The game guards this with `is Dictionary`; here it is
> `isinstance(split, dict) and split`.

## Three things measured, not read

Each of these is somewhere the code alone is misleading. All are pinned by tests.

**Symbols are sized 50px, not the label's 40.** `base_card.gd:979` calls
`Utils.replace_icon_from_dict(resolved_base)` with **no size argument**, so every
card symbol uses that function's `font_size = 50` default. Confirmed by rendering
one token on an otherwise identical card in Godot and differencing against a
blank one: base height is exactly 1.25 × the label's 40px font. Sizing off the
label makes every symbol 20% too small and changes line breaks.

**Godot's advance is ~2.6% wider than PIL's.** Measured on `"per card drawn"` at
size 40: Godot 321px, PIL 312.8px. Cap heights match exactly, so this is spacing,
not scale — but it flips break decisions on near-full lines.
`card_layout.wrap_width()` shrinks the wrap width by that ratio.

**The valence outline is black.** `ValenceLabel` sets `outline_size = 3` but not
`font_outline_color`, so it takes Godot's black theme default — black text with a
black outline reads as *bold*, not as an outline. `NameLabel` is the opposite: it
explicitly overrides the outline to white.

## Deck and hand layouts

`/render_deck` tiles every card in a deck; `/render_hand` fans a random sample;
`/search` and `/render` on a deck reuse the same grid. All are **static, opaque
PNGs** — unlike a single card, a sheet reads better with a background separating
the tiles, and it cannot be transparent: the gutters between tiles would show
the theme through and the grid would read as holes. A 110-card deck animating at
60 frames each would be tens of megabytes and unreadable at thumbnail size, so
animation stays on `/render`, where one card fills the message.

### One tile shape for every kind

`/search` draws cards, aspects and rites on one sheet, and until 2026-08-28
`_faces` resized every face to `(width, CARD_H × width / CARD_W)`. A card face
**is** that shape, so cards looked correct and the bug stayed invisible on a
deck sheet, which is cards only. A mixed sheet showed both halves of it:

| | Face | Forced to | Result |
|---|---|---|---|
| Card | 560×897 canvas (1.602) | 1.602 | Right shape, but ~63px of empty canvas scaled down with it — the body drew **smaller** |
| Aspect / rite | 544×759 crop (1.395) | 1.602 | **15% too tall**, and filling the whole tile, so it drew **larger** |

`_faces` now crops each face to its own silhouette first, then scales by width
with that face's own ratio — the same two steps `_comparison_sides` has always
done. Cropped, the two silhouettes agree to within 1% (552×766 against
544×759), so the tiles come out the same size and shape without either being
distorted to get there. The residual pixel or two of height is **padded**, never
scaled away; stretching to match is the defect this removes.

**That background is black** (`deck_render.SHEET_BG`), since 2026-08-28. It was
two different greys before: the sheet filled with `(30, 31, 34)` while every
tile on it was flattened onto `card_render.DISCORD_BG` `(49, 51, 56)`, so each
card wore a faintly lighter rectangle around it — visible in Discord, invisible
in the code, because the two constants lived in different modules. Both were
Discord dark-theme colours, matching no theme exactly on a client set to Darker,
Midnight or Light. `DISCORD_BG` had no callers left afterwards and was removed.
The comparison sheet behind `/show` shares `SHEET_BG`, label band included.

**Art fetching is the bottleneck, not drawing** — measured at 0.68s per card
downloading versus 0.04s rendering. `deck_render.fetch_art_many()` parallelises
downloads across 12 threads and deduplicates by filename, since decks repeat art.
That takes a 110-card deck from ~79s serially — past the command timeout — to
about 27s. `/render_deck` runs on a **120s** timeout to leave room for a cold
process paying DNS and TLS.

A download that fails maps to `None` and the card renders without art rather than
sinking the whole sheet.

| | Default | Notes |
|---|---|---|
| Grid | 10 columns, 200px cards | 110 cards → 2110×3640, ~2 MB |
| Grid cap | 200 cards | Refused with a message rather than silently truncated |
| Hand | 6 cards, 300px, 26° spread | Seeded draws are reproducible |

The hand fans about a pivot **below** the cards, so they splay from a common
point. Rotating each card about its own centre reads as scattered rather than
fanned. The horizontal step is 0.80 of a card width — a held hand in-game
overlaps far more, but there you hover to read a card; in a screenshot the name
and rules text have to survive the overlap, and both sit on the card's left.

**Non-card deck items are skipped** and the count is reported in the reply.
Aspects and events would need the fate renderer.

## Aspects and rites

`/render` dispatches aspects and rites to `fate_render.py`. Three content types,
three shapes:

| | Card | Aspect | Rite |
|---|---|---|---|
| Background | Static PNG + element border | One shader pattern, tinted per aspect | One of **four** shader patterns |
| Art | `.exr` or PNG, 275×275 | `.exr`, 210×210 | **None** — the Image node is hidden |
| Valence | Yes, plus a split face | No | No |
| Colours | Element-driven | `image_data` primary/secondary | Fixed in the scene |
| Animates | `.exr` cards | Yes | No |

### The backgrounds are pre-rendered

Unlike a card's static PNG, aspect and rite backgrounds are **procedural
shaders**. AzothBot cannot run a shader, so they are exported once from Godot and
vendored under `assets/card_art/backgrounds/` (~3.3 MB, 10 files — one aspect
background, four baked rite backgrounds, four rite masks, one animated mask):

```bash
godot --path ../azoth tools/BackgroundExportTool.tscn -- --out=/tmp/bgs
cp /tmp/bgs/*.png assets/card_art/backgrounds/
```

**Aspects need only one.** `aspect_card.gd` sets `u_color`, `is_shiny` and two
attuned-state params — the pattern itself comes from the material's defaults, so
every aspect shares it. Verified at **0.99+ structural correlation** across three
aspects over pure-background regions. It is exported **white** so Python can
multiply in each aspect's `image_data.primary_color`; exporting it pre-tinted
would bake one aspect's hue into all 149.

**Rites need eight — four baked, four as recolourable masks.**

`event_card.gd::set_event_visuals()` picks one of four materials by **display
name** (Smith/Upgrade, Trash/Sever, Rest/Heal, everything else). That mapping is
data, not a field, so `fate_layout.RITE_BACKGROUND_BY_NAME` tracks that match
statement by hand.

But those four are only the **baseline**. A rite's `image_data` may override
`background_color`, `primary_color` and `secondary_color` — **21 of the 44 live
rites do** — and `reactant_card.gdshader` composes them as:

```glsl
col    = mix(primary_color, secondary_color, combinedPattern)
output = mix(background_color, col, pattern * pattern)
```

So a baked PNG is right only for a rite that overrides nothing. The **mask**
export renders each variant with background = red, primary = blue, secondary =
green, so each channel carries one term:

```
R = 1 - pattern²      G = pattern² · cp      B = pattern² · (1 - cp)
```

from which `pattern² = G + B` and `cp = G / (G + B)`. Python then composes with
whatever colours the rite actually carries. Feeding the material's own colours
back through reconstructs the baked export to **0.003 mean error** — the residue
being the shader's dither and its TIME terms.

Rites with no palette use the baked PNG directly, so they need no reconstruction.

### Rites animate too

`reactant_card.gdshader` modulates its pattern amplitudes from `TIME`
(`getModulatedAmplitude`, `noise_speed = 0.5` on the attribute material), so the
blobs breathe. The animated mask is a **30-frame WebP** at 15fps (806 KB),
recoloured per rite exactly as the still path is.

**Only the `attribute` variant is exported animated.** All 21 rites in the live
"Rites" deck resolve to it; the other three back **boons**
(Boon_Left/Center/Right), which are a different mechanic, and stay static.

**Ping-pong, not cross-fade.** The shader's noise is not periodic, so there is no
natural loop — and unlike the eigenfunction art there is no smooth field to blend
in: the pattern has crisp edges, so blending two frames would ghost. Playing the
sequence forward then backward loops exactly, and reads naturally because the
motion is amplitude modulation rather than travel. 30 source frames become a
58-frame, 3.9s loop at no extra vendored cost.

Output is ~1.9 MB — larger than a card's 700 KB because the *whole* card changes
each frame, so GIF's frame differencing has nothing to elide. Colour reduction
does not help (the palette is already 2–3 colours); only frame count does.

> **Exporting frames needs a warm-up.** The SubViewport does not begin redrawing
> until the engine has been running a couple of seconds. Captures taken before
> that are byte-identical, which looks exactly like a shader with no animation —
> the export tool waits 150 frames first for this reason.

### A rite's palette also tints its text

`event_card.gd::set_event_text_color()` overrides **both** the name and the rules
text — and the name's outline — with `text_color` if authored, else
`primary_color`. A rite with no palette keeps the scene's blue name and orange
text.

Verified against Godot: Amplification's name renders at exactly `#ffb01f`, its
`primary_color`; Etching's at `#8f2a14`; Sever and Echo, which author nothing,
stay at the scene's `#04b7ff`.

`text_color` exists so a row whose pattern colour is too dark to read can
override just the foreground — it must win over `primary_color`.

Re-export only when the aspect shader or the rite materials change. `sync_assets`
does **not** cover them — it has no Godot.

> **These exports are full-VIEWPORT captures, not raw textures**, and the
> difference matters. They already carry the card silhouette — rounded corners
> and all — at its final position (x 8–551, y 69–827 of the 560×897 viewport), so
> they are composited at the ORIGIN at native size.
>
> A card's background is the opposite: a raw texture file that has to be fitted
> into its node's box. Fitting the aspect/rite captures the same way stretches
> them to the node's 660×897 and pushes the rounded edges off-canvas, leaving
> **square-cornered cards**. `fate_layout.ASPECT_BACKGROUND_NODE` keeps the
> scene's box for reference; `ASPECT_BACKGROUND` is what to draw into. Pinned by
> `test_backgrounds_keep_the_card_silhouette`.

### Two nodes the scenes ship hidden

Both are `visible = false`, and drawing them produces a card the game never
shows:

- **`Type`** on both scenes ("Item" / "Event"). The rite's box sits at y 811–868,
  past the card's opaque extent — it would render *below* the card.
- **`Image` on rites.** A rite's visual IS its background pattern. The `image`
  column feeds the draft thumbnail, not the card face.

### Aspect colours are reversed

From `GlobalVars.get_eigenfunction_colors()` and `aspect_card.gd`:

- The **name label** takes `secondary_color`.
- The **art's accent zone** takes `primary_color`, and its base takes
  `secondary_color`.

So label and art key off opposite fields — the reverse of the card convention,
and the easiest thing here to get backwards since either way produces a
plausible-looking card. Pinned by a test.

## Naming: rite vs event

**"Rite" is the current name for what the database calls an "event."** The rename
landed 2026-08-26 in code and commands only.

Not every `content_type: event` row is a rite. The live rites are the **21 in the
"Rites" deck** (`usage_type = rite`), and they are exactly the 21 that carry a
palette. `Boon_Left/Center/Right` hold nine more events — Augury, Echo, Sever and
the `Random *` set — which are **boons**, a different mechanic. `/render`
covers everything with `content_type: event`, boons included, since the command
is for inspecting content.

| Says `rite` | Says `event` |
|---|---|
| Commands (`/create_rite`, `/update_rite`, …) | `events` table |
| `azoth_commands/rites.py` | `content_type` value |
| `fate_render`, `fate_layout` | `eventimages` bucket |
| Everything user-facing | `event_card.tscn` and the game's scripts |

`rites.py` marks the boundary with `TABLE_NAME`, `DB_KEY` and `MODEL_NAME`; the
first two are what change when the tables are eventually renamed. A test asserts
all three, so the boundary cannot drift silently.

## Retired

**Hero commands** (`/create_hero`, `/render_hero`, …) were unregistered
2026-08-26. `azoth_commands/heroes.py` still exists but its attacher is
deliberately not called from `__init__.py` — do **not** "fix" that the way you
would for a module left out by accident. Hero cards were also never ported to the
new renderer, so `/render_hero` would draw the wrong frame.

Two modules are kept as **archives**, both marked at the top of the file. Neither
is reachable at runtime; both record templates the game has replaced.

| File | Superseded by | Note |
|---|---|---|
| `azoth_logic/card_renderer.py` | `card_render.py` + `deck_render.py` | Its only importer is the retired `heroes.py`, which is itself never imported |
| `azoth_logic/fate_renderer.py` | `fate_render.py` | Nothing imports it. Was `ritual_renderer.py` before the 2026-08-26 rename |

Do not wire either back up. If hero cards are rendered again they should go
through `card_render.py` against `hero_card.tscn`.

## Caching

`azoth_logic/art_cache.py` holds two on-disk caches under `cache/` (gitignored;
deleting it is always safe). Art fetching is ~95% of every render's cost —
measured at 0.68s per item downloading versus 0.04s drawing.

| Cache | Keyed by | Why |
|---|---|---|
| **Art** | `(bucket, filename)`, with a 7-day TTL | Storage uploads are flat-named and **upserting**, so a filename's content changes when art is regenerated. The TTL bounds how long that goes unnoticed |
| **Renders** | A content hash: the item's rendered fields + the art bytes + `RENDERER_VERSION` + render params | Only **animated** results are cached. A still is 0.04s and not worth the bookkeeping; a GIF is 1.3–2.8s |

Measured effect: `/render` 1.77s → 0.00s on a repeat, `/render_deck` 1.35s →
0.38s.

**Art is dropped explicitly when it is re-uploaded.** `supabase_storage.upload_image`
writes a FLAT name with `x-upsert: true`, so `regenerate_image=True` puts new
bytes behind an unchanged filename — the one case a `(bucket, filename)` key
cannot see. `create_card` and `update_card` call `art_cache.forget_art()` at the
upload site; without it the 7-day `ART_TTL` is how long the old art keeps
showing.

> **The render key is a content hash on purpose.** The old renderer keyed on card
> *name*, so editing a card kept serving its previous image. Here, changing the
> text, the valence, the element, the art, or the renderer version all produce a
> different key. Bump `RENDERER_VERSION` whenever the renderer's output changes.

Both writes go through a temp file plus `os.replace`, so a crash mid-write cannot
leave a truncated file that later reads as valid. Every cache operation swallows
`OSError` — a cache is never worth failing a render over.

### Eviction

**Size-capped, on write, not on a timer.** Art 300 MB, renders 400 MB, evicting
oldest-first down to 80% of the cap so eviction runs on roughly every other write
rather than every one. The invariant is *never above the cap* — the cache
oscillates between the target and the cap by design.

The two caches grow completely differently, which is why only one of them
actually needs the policy:

| | Bounded? | Why |
|---|---|---|
| **Art** | **Yes, ~250 MB** | Keyed by `(bucket, filename)`, and the content pool is finite — at most one file per item. Measured at 579 KB per `.exr` across ~395 animatable items. Its cap is a backstop that should never fire |
| **Renders** | **No** | Keyed by a content *hash*, so every edit to an item orphans its previous render permanently, at ~1.96 MB a time. This cap is the real control |

**Why not a daily sweep.** Two reasons, both specific to this bot:

- It is **hand-started and not reliably always-on** ([DEPLOYMENT.md](DEPLOYMENT.md)),
  so a timer may not fire for weeks — exactly when growth has accumulated. Tying
  eviction to the thing that *causes* growth makes the cap an actual ceiling.
- Growth is **bursty, not time-proportional**. One `/bulk_update` plus a
  re-render sweep can add hundreds of megabytes in a minute; a sweep every 24h
  permits an arbitrarily large spike in between.

**Why LRU and not age.** A cache entry's value is not correlated with its age.
The card someone renders every day is the *most* valuable entry in the cache, and
a "delete anything older than a week" rule deletes it while keeping something
rendered once, six days ago, and never touched since.

> **`mtime` means two different things, and that is deliberate.**
>
> Renders are keyed by a content hash, so they can never go stale — which frees
> `mtime` to mean **last used**. `get_render` touches on a hit, because
> filesystem `atime` cannot be relied on (most systems mount `relatime` or
> `noatime`).
>
> `get_art` must **not** do the same. There `mtime` is the *fetch* time that
> `ART_TTL` measures, and touching it on a hit would mean art never expires —
> and since Storage uploads are flat-named and upserting, never expiring means
> never noticing the bytes changed. Both directions are pinned by tests.

`/cache status` shows both caches against their caps; `/cache clear` drops them
(authorized only, and `renders` alone is usually what you want — art is expensive
to re-download and bounded anyway).

## Assets

Vendored under `assets/card_art/` (~2 MB) so the bot runs standalone — the host
has no Godot and no game checkout.

```bash
python -m tools.sync_assets --azoth ../azoth
python -m tools.sync_assets --azoth ../azoth --dry-run   # report only
```

It copies the borders, background and every symbol the tokens reference, and
regenerates `assets/card_art/card_symbols.json` by parsing `Utils.replace_dict`
out of `utils.gd` — so a new symbol token needs a sync, not a code change.

**It does not copy the aspect and rite backgrounds, and cannot.** They are shader
output, not files in the game repo — see [The backgrounds are
pre-rendered](#the-backgrounds-are-pre-rendered). What it *does* do is verify
they are present and exit non-zero when one is missing, naming the export command
rather than sending you round the loop again:

```
10 shader-exported background(s) present (not managed by this script)
```

`missing_asset_hint()` in `azoth_commands/helpers.py` applies the same split at
runtime: a missing symbol says "run sync_assets", a missing background says
"re-export from Godot".

## When the card template changes

**The sync script cannot detect layout changes.** It copies art. Everything else
is hand-maintained, and this is the second time the renderer has gone stale, so
here is the checklist:

1. **Re-run the sync.** New or renamed art and any new symbol tokens.
2. **Diff `card.tscn` against `card_layout.py`.** Every box there is
   `centre + offset` from the scene. Node moved, resized, font changed → update
   the constant.
3. **Check the render path in `base_card.gd`.** `configure_display()`,
   `apply_card_image()` and `_render_dynamic_text()` are where behaviour lives.
4. **Re-verify the measured constants above** if text or symbols look off. The
   method that produced them: render a card in Godot, render the same card here,
   and difference the two.
5. **Run the tests.** `pytest tests/test_card_render.py` fails on layout drift —
   but only where a constant is asserted on. There is no golden-file comparison
   against Godot in the suite (see [TESTING.md § Gaps](TESTING.md#gaps)), so a
   node that moves without changing a pinned constant will not be caught.

### Comparing against Godot

`tools/card_render_tool.gd` + `CardRenderTool.tscn` in the **azoth** repo render
cards through the game's own scene, which is what the constants above were
calibrated against:

```bash
godot --path . tools/CardRenderTool.tscn -- --in=cards.json --out=/tmp/ref
```

Needs a real display driver — `--headless` installs the dummy renderer, whose
`texture_2d_get()` returns null, so viewport captures come back empty. This is a
development aid only; the bot never calls it.

## Known differences from the game

- **Line breaks can differ by a word.** PIL and Godot shape text differently; the
  advance correction gets most of the way, not all.
- **Long text overflows the card bottom.** The game does this too — verified on
  `Multmaxer` — so it is reproduced rather than fixed.
- **Subtype-less cards render a blank line here, `Arcane` in-game.** That is a
  *game* bug: `card.tscn` ships `TypeLabel` with `text = "[center]Arcane"` and
  `base_card.gd` only overwrites it when `subtypes` is non-empty, so the scene
  placeholder leaks through on every subtype-less card (`Multmaxer`, `Five 5`,
  and others). Not reproduced.
