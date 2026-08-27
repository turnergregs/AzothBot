# Rendering

> ## ⚠️ Legacy
>
> **The renderers described on this page were superseded on 2026-08-26.** Both are
> archives now — unreachable at runtime, kept as the record of a card template the
> game has replaced. For how cards are drawn TODAY, read
> [CARD_RENDERING.md](CARD_RENDERING.md).
>
> What is still current on this page: **art generation** (`image_generator.py` →
> `eigenfunction_generator.py`) and the **Storage bucket** layout. `create_*` and
> `update_* regenerate_image=True` still go through them.

How AzothBot turns a database row into a card image, and where those images live.

Two independent renderers, both in `azoth_logic/`, both compositing with Pillow at
print resolution (900 PPI, 8.5 mm bleed):

| Renderer | Handles | Size |
|---|---|---|
| `CardRenderer` (`card_renderer.py`) | Cards, and the multi-card layouts | 1,176 lines |
| `FateRenderer` (`fate_renderer.py`) | Aspects and events — two-sided cards | 1,526 lines |

These are the best-documented modules in the repo (~40 docstrings, ~540 comment
lines between them). Read the code for detail; this page covers how the pieces fit
and the parts that aren't obvious from the source.

## Two separate steps

They are often confused because both produce a PNG.

**1. Art generation** — `azoth_logic/image_generator.py` → `eigenfunction_generator.py`.
Produces the abstract background artwork for a card. **Random**: the same card
generates different art on each run.

**2. Card rendering** — `card_renderer.py`. Composites that artwork with the frame,
title, rules text, valence shape, element icons and typography into a finished
card face. **Deterministic** given the same inputs.

Generation happens on `create_*`, and on `update_*` only when
`regenerate_image=True`. **That half is still live.** The rendering half is not:
`/render` goes through `card_render.py` / `fate_render.py` now, and nothing calls
`CardRenderer` or `FateRenderer` at all — see
[CARD_RENDERING.md](CARD_RENDERING.md).

## Where the art comes from

`RandomEigenfunctionGenerator` loads precomputed eigenfunctions of the Laplacian
on 2D domains — solutions to the wave equation on odd-shaped membranes — and
renders their level sets as the card background.

Each set is three files in `eigenfunctions/`:

```
<name>_eigenfunctions.npy    the eigenfunctions
<name>_eigenvalues.npy       their eigenvalues
<name>_solver_data.npz       mesh points and elements, for interpolation
```

A set is only usable if **all three** are present — the constructor scans for
`*_eigenfunctions.npy` and checks for the siblings. If none qualify it raises
`FileNotFoundError` at import time, which takes the whole bot down at startup,
since `image_generator.py` instantiates the generator at module level to cache it.

Interpolation is `matplotlib.tri.CubicTriInterpolator` over a `Triangulation` of
the solver mesh.

### Element colours

Set in `RandomEigenfunctionGenerator.__init__`:

| Element | RGBA |
|---|---|
| `blood` | `(255, 0, 0, 255)` — red |
| `sol` | `(249, 164, 16, 255)` — gold |
| `anima` | `(135, 105, 233, 255)` — purple |
| `dark` | `(0, 0, 0, 255)` |
| `light` / `all` | `(255, 255, 255, 255)` |

Background is `(12, 12, 12, 0)` — near-black, transparent.

A card with no element falls back to `light`. Note that in `generate_image` the
`is_dark` branch is a no-op — both arms return `"light"`.

## Storage layout

Three path maps in `constants.py`, keyed by content type:

| Map | Purpose | Example |
|---|---|---|
| `ASSET_RENDER_PATHS` | Local finished renders | `assets/renders/cards` |
| `ASSET_DOWNLOAD_PATHS` | Local cache of images pulled from Supabase | `assets/downloaded_images/cards` |
| `ASSET_BUCKET_NAMES` | Supabase Storage bucket | `cardimages` |

Buckets: `cardimages`, `aspectimages`, `eventimages`, `heroimages`.
(`ritualimages` and `consumableimages` were dropped from the maps on 2026-08-26;
the buckets themselves may still exist in Storage.)

`ASSET_RENDER_PATHS` also has `deck` and `hand` entries, which have no bucket —
multi-card layouts are rendered and posted to Discord, never stored.

## Naming

Names are slugged with `re.sub(r'\W+', '_', name.lower()).strip('_')`.

- **Upload** (`supabase_storage.upload_image`) writes a **flat** name —
  `catalyst_of_anima.png` — with `x-upsert: true`, so re-uploading overwrites.
- **Download** strips any `_<version>` suffix, so `catalyst_of_anima_2.png` lands
  locally as `catalyst_of_anima.png`.
- `generate_image_filename(name, version)` produces versioned names but is not on
  the current upload path.

> ⚠️ `download_image`'s docstring claims it saves "with a timestamped filename".
> It doesn't — it writes the flat name. Stale comment.

Because uploads are flat and upserting, **there is no image history**. Regenerating
art destroys the previous version. `.gitignore` excludes `*.png`, so nothing is
recoverable from git either.

## Multi-card layouts

`CardRenderer` also builds composites, used by `/render_deck` and `/render_hand`:

| Method | Output |
|---|---|
| `create_tiled_image(cards, path)` | Flat tiling |
| `create_card_grid(cards, path, num_cards)` | Grid layout |
| `create_sample_hand(cards, path, num_cards=6, spread_angle=30)` | Fanned hand, 30° spread |

Both deck commands carry a **60-second timeout** rather than the usual 5–15 —
rendering a full deck at 900 PPI is slow. A large deck can still exceed it, and
the failure surfaces as "⏰ Timed out."

## Assets

```
assets/fonts/Aldrich-Regular.ttf     the card typeface
assets/icons/{Anima,Blood,Sol}.png   element icons
assets/icons/{view,dark_view}.png    ritual view markers
assets/renders/                      local render output
assets/downloaded_images/            local download cache
assets/rendered_rituals/             ritual output
```

`combinations/` (294 files) and `output/` hold generated samples from art
experiments. Neither is used at runtime.

## Adding a renderable content type

Steps 1–3 are about ART GENERATION and still apply. Step 4 is where this page
stops being current — drawing a card face is
[CARD_RENDERING.md](CARD_RENDERING.md)'s subject now.

1. Add entries to all three maps in `constants.py`.
2. Create the Supabase Storage bucket.
3. Call `generate_and_upload_image(record, bucket)` from the `create_*` command,
   then `art_cache.forget_art(bucket, file_path)` — uploads are flat-named and
   upserting, so the cache cannot see that the bytes changed.
4. Give the type a layout module and a render function, add it to
   `content_index.TABLES` so `/show` and `/render` can reach it, and to
   `deck_render._bucket_for` / `_still_for` so it can appear in a `/search` grid.

`generate_and_upload_image` in `azoth_commands/helpers.py` wraps generate → read
bytes → upload, and takes an optional `ritual_side` to pick
`challenge_name` / `reward_name` instead of `name`.

## Gotchas

- **Art is random.** Regenerating gives different output. There is no seed
  parameter and no way to reproduce a previous image.
- **The generator is a module-level singleton** in `image_generator.py`, cached to
  avoid reloading the `.npy` data per call. A missing `eigenfunctions/` directory
  is a startup crash, not a command-time error.
- **Rendering is CPU-bound and synchronous.** It blocks the event loop; the bot
  cannot respond to anything else while a deck renders.
- **Aspects have no `regenerate_image`** — its parameter is commented out, and aspects take an
  existing image name rather than generating art.
- **The fate renderer's name is historical.** It was `RitualRenderer` until
  2026-08-26. Rituals were the precursor to Aspects; the content type is retired
  but this renderer was never ritual-specific — `aspects.py` and `rites.py` (then
  `events.py`) always used it. Its `render_card_sides` / `render_ritual_card` methods still
  carry the old vocabulary internally.
