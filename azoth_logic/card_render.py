"""Render an Azoth card face to a PNG or an animated GIF.

Reproduces `scenes/cards/card.tscn` from the azoth repo closely enough for
Discord. Deliberately NOT reproduced:

  * `base_card_shader.gdshader` -- tilt, specular, drop shadow, holographic
    sheen. All are responses to being hovered or moved in-game.
  * Enhancements and attributes. Both are applied during a run and never present
    on a card as authored.

Two art paths, because the content has two: a card whose `image` ends in `.exr`
carries eigenfunction art and animates; anything else is a flat PNG and does not.
That split mirrors `ImageCache.eigenfunction_name_for_image()`.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from azoth_logic import art_cache
from azoth_logic import holo
from azoth_logic import card_layout as L
from azoth_logic import eigenfunction_art as ef
from azoth_logic import rich_text

ASSET_ROOT = Path(__file__).resolve().parent.parent / "assets"
BORDER_DIR = ASSET_ROOT / "card_art" / "borders"
FONT_PATH = ASSET_ROOT / "fonts" / L.FONT_FILE

# Discord's dark-theme message background. Still used by the multi-card SHEETS
# (deck_render), which are opaque RGB by design -- a grid reads better with a
# background separating the cards.
#
# Single cards no longer use it: they carry real transparency. See to_gif().
DISCORD_BG = (49, 51, 56)

# --- Animated output -------------------------------------------------------
#
# GIF alpha is 1 bit, so every pixel is either fully drawn or fully absent and
# the card's antialiased rim has to be cut somewhere. Measured on a rendered
# face, that rim is only ~3px wide and 98% of the card is fully opaque, so
# cutting at the halfway point is imperceptible -- and it buys a card that sits
# correctly on every Discord theme instead of on a grey rectangle.
ALPHA_CUTOFF = 128

# Palette entries. The last index is reserved for transparency, so this is
# (colours + 1) in GIF terms.
GIF_COLORS = 64

# ONE palette for the whole animation, not one per frame.
#
# Per-frame adaptive palettes defeat the GIF optimiser's frame differencing: it
# can only encode a changed sub-rectangle when successive frames share a colour
# table. Measured on Restoration (60 frames): per-frame palettes give 806 KB,
# a shared palette 272 KB -- and 272 KB is smaller than the 453 KB the old
# FLATTENED output cost, so transparency came out cheaper than not having it.
#
# The palette is derived from frames sampled across the animation rather than
# from the first frame, which would miss colours that only appear later.
GIF_PALETTE_SAMPLES = 8

# A rite is a TWO-TONE design -- one pattern colour over one background -- and
# every renderer passes GIF_COLORS except that one. Its frames measure ~4,680
# distinct colours, but they are all antialiasing between two hues, so they
# quantise to 16 with no visible difference (checked side by side at 16/32/64
# against the source). It matters because a rite's WHOLE background changes each
# frame, so frame differencing cannot help it and the palette is the only
# control: 3.0 MB at 64 against 1.7 MB at 16.
RITE_GIF_COLORS = 16

_font_cache: dict = {}
_border_cache: dict = {}


def _font(size: int) -> ImageFont.FreeTypeFont:
    if size not in _font_cache:
        _font_cache[size] = ImageFont.truetype(str(FONT_PATH), size)
    return _font_cache[size]


def _layer(file_name: str, box) -> Image.Image:
    """Load a full-card layer and scale it to its card.tscn box."""
    key = (file_name, box)
    if key in _border_cache:
        return _border_cache[key]
    img = Image.open(BORDER_DIR / file_name).convert("RGBA")
    _, _, w, h = box
    img = img.resize((round(w), round(h)), Image.LANCZOS)
    _border_cache[key] = img
    return img


def _border(card: dict) -> Image.Image:
    """The element border, with a split card's second border blended over it.

    Port of `card_border_dim.gdshader`'s fragment stage:

        coverage = clamp(split.a / max(base.a, 1e-4), 0, 1)
        rgb      = mix(base.rgb, split.rgb, coverage)
        alpha    = base.a

    Two details that a plain alpha-composite would get wrong, and which the
    shader's own comments call out:

      * Coverage is the split's alpha NORMALISED BY THE BASE'S, not the split's
        alpha directly. That keeps the split opaque along the antialiased rim --
        where both textures fade together -- while preserving the authored
        interior fade.
      * Alpha comes from the BASE alone. The split must not extend the
        silhouette by a single pixel, or the card gets a second, fatter outline.

    The dim_color / base_dim / split_dim uniforms are skipped: they fade the side
    you are not hovering, which is in-game state with no meaning in a snapshot.
    """
    base = _layer(L.border_file(card.get("element")), L.BORDER)
    face = L.split_face(card)
    if face is None:
        return base
    split_file = L.split_border_file(face[0])
    if split_file is None:
        return base

    import numpy as np
    b = np.asarray(base, dtype=np.float32) / 255.0
    sp = np.asarray(_layer(split_file, L.BORDER), dtype=np.float32) / 255.0
    coverage = np.clip(sp[..., 3] / np.maximum(b[..., 3], 1e-4), 0.0, 1.0)[..., None]
    rgb = b[..., :3] * (1.0 - coverage) + sp[..., :3] * coverage
    out = np.dstack([rgb, b[..., 3]])
    return Image.fromarray((out * 255.0).round().astype("uint8"), "RGBA")


def _base_face(card: dict) -> Image.Image:
    """Everything except the art: background, border, valence, name, subtype, text.

    Drawn once and reused across animation frames -- only the art moves.
    """
    canvas = Image.new("RGBA", (L.CARD_W, L.CARD_H), (0, 0, 0, 0))

    bg = _layer(L.BACKGROUND_FILE, L.BACKGROUND)
    canvas.alpha_composite(bg, (round(L.BACKGROUND[0]), round(L.BACKGROUND[1])))
    canvas.alpha_composite(_border(card), (round(L.BORDER[0]), round(L.BORDER[1])))

    draw = ImageDraw.Draw(canvas)
    colour = L.element_color(card.get("element"))

    # --- Valence, in the corner wedge the border art provides ---------------
    valence = card.get("valence")
    if valence is not None:
        vx, vy, vw, vh = L.VALENCE_REL
        box = (L.SYMBOL[0] + vx, L.SYMBOL[1] + vy, vw, vh)
        _draw_centered_line(draw, str(int(valence)), _font(L.VALENCE_SIZE), box,
                            L.VALENCE_COLOR, outline=L.VALENCE_OUTLINE,
                            outline_color=L.VALENCE_OUTLINE_COLOR)

    # A split card's second valence, in the opposite corner. Hidden by default
    # in card.tscn and only shown by set_split_card_visuals().
    face = L.split_face(card)
    if face is not None and face[1] is not None:
        vx, vy, vw, vh = L.VALENCE2_REL
        box = (L.SYMBOL[0] + vx, L.SYMBOL[1] + vy, vw, vh)
        _draw_centered_line(draw, str(int(face[1])), _font(L.VALENCE_SIZE), box,
                            L.VALENCE_COLOR, outline=L.VALENCE_OUTLINE,
                            outline_color=L.VALENCE_OUTLINE_COLOR)

    # --- Name + subtype, the VBoxContainer ----------------------------------
    # The VBox centres its children vertically as a group, with a fixed
    # separation. Measure both, then place the stack.
    name_font, sub_font = _font(L.NAME_SIZE), _font(L.SUBTYPE_SIZE)
    tx, ty, tw, th = L.TITLE_BOX

    name_lines = rich_text.layout(str(card.get("name", "")), name_font, L.wrap_width(tw))
    name_h = rich_text.measure(name_lines, name_font, 0)

    subtypes = card.get("subtypes") or []
    subtype = str(subtypes[0]) if subtypes else ""
    sub_h = rich_text.measure(
        rich_text.layout(subtype, sub_font, L.wrap_width(tw)),
        sub_font, 0) if subtype else 0

    stack_h = name_h + (L.TITLE_SEPARATION + sub_h if subtype else 0)
    y = ty + max(0.0, (th - stack_h) / 2)

    rich_text.draw_centered(canvas, name_lines, name_font, (tx, y, tw, name_h),
                            L.NAME_COLOR, stroke=L.NAME_OUTLINE,
                            stroke_color=L.NAME_OUTLINE_COLOR)
    if subtype:
        y += name_h + L.TITLE_SEPARATION
        rich_text.draw_centered(
            canvas, rich_text.layout(subtype, sub_font, L.wrap_width(tw)),
            sub_font, (tx, y, tw, sub_h), colour)

    # --- Rules text ---------------------------------------------------------
    # TextLabel is fit_content with size_flags_vertical = SHRINK_CENTER, so it
    # grows about the CENTRE of its declared box rather than downward from the
    # top. A three-line card sits ~46px higher than a naive top-aligned draw.
    text = str(card.get("text") or "")
    if text:
        tf = _font(L.TEXT_SIZE)
        bx, by, bw, bh = L.TEXT_BOX
        lines = rich_text.layout(text, tf, L.wrap_width(bw))
        text_h = rich_text.measure(lines, tf, 0)
        rich_text.draw_centered(canvas, lines, tf,
                                (bx, by + (bh - text_h) / 2, bw, text_h),
                                L.TEXT_COLOR)
    return canvas


def _draw_centered_line(draw, text, font, box, fill, outline=0, outline_color=(0, 0, 0)):
    x, y, w, h = box
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    px = x + (w - (right - left)) / 2 - left
    py = y + (h - (bottom - top)) / 2 - top
    draw.text((px, py), text, font=font, fill=fill,
              stroke_width=outline, stroke_fill=outline_color)


def is_animated(card: dict) -> bool:
    """Only eigenfunction (.exr) art animates -- 246 of 400 cards."""
    return str(card.get("image") or "").lower().endswith(".exr")


def _art_still(card, art_bytes):
    primary, secondary = ef.colors_for_card(card)
    if is_animated(card):
        with _temp(art_bytes, ".exr") as p:
            return ef.still(p, primary, secondary)
    img = Image.open(io.BytesIO(art_bytes)).convert("RGBA")
    return img.resize(ef.ART_SIZE, Image.LANCZOS)


class _temp:
    """OpenCV's EXR reader takes a path, not bytes."""
    def __init__(self, data, suffix):
        import tempfile
        self.f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        self.f.write(data)
        self.f.close()

    def __enter__(self):
        return self.f.name

    def __exit__(self, *exc):
        import os
        try:
            os.unlink(self.f.name)
        except OSError:
            pass


def render_still(card: dict, art_bytes: bytes | None) -> Image.Image:
    """The card as a single RGBA frame."""
    face = _base_face(card)
    if art_bytes:
        face.alpha_composite(_art_still(card, art_bytes),
                             (round(L.ART[0]), round(L.ART[1])))
    return face


def alpha_bbox(frames, cutoff: int = ALPHA_CUTOFF):
    """The tightest box containing every frame's opaque pixels.

    Taken over the UNION of all frames so the crop is identical for each one --
    cropping per frame would make the card jitter as the art moves.
    """
    import numpy as np
    acc = None
    for f in frames:
        mask = np.asarray(f)[..., 3] >= cutoff
        acc = mask if acc is None else (acc | mask)
    ys, xs = np.where(acc)
    if not len(xs):
        return (0, 0, frames[0].width, frames[0].height)
    return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


def _global_palette(frames, colors: int):
    """One adaptive palette covering the whole animation.

    Built from a montage of sampled frames so a colour that only appears late
    still gets an entry.
    """
    step = max(1, len(frames) // GIF_PALETTE_SAMPLES)
    sample = frames[::step]
    w, h = frames[0].size
    strip = Image.new("RGB", (w, h * len(sample)))
    for i, f in enumerate(sample):
        strip.paste(_on_black(f), (0, i * h))
    quantised = strip.convert("P", palette=Image.ADAPTIVE, colors=colors)

    # PAD to exactly `colors` entries before appending the transparent one.
    #
    # getpalette() returns only the entries actually USED, so a low-colour
    # animation comes back short -- a rite background is 2-3 colours and yields
    # four entries. Appending the transparent entry to a short palette puts it at
    # index 4 while the pixel data references index 64, which is a GIF whose
    # pixels point past its own colour table. Pillow happens to tolerate reading
    # that back; a stricter decoder need not, and the transparent index would
    # then resolve to nothing.
    palette = quantised.getpalette()[: colors * 3]
    palette += [0, 0, 0] * (colors - len(palette) // 3)
    return palette + [0, 0, 0]                  # index `colors` == transparent


def _on_black(frame: Image.Image) -> Image.Image:
    """RGB for quantisation. Black, not DISCORD_BG: pixels below the cutoff are
    replaced by the transparent index anyway, so the matte only has to be a
    colour that does not pull the palette toward a background nobody sees."""
    return Image.alpha_composite(
        Image.new("RGBA", frame.size, (0, 0, 0, 255)), frame).convert("RGB")


def to_gif(frames, fps: int = 15, colors: int = GIF_COLORS,
           cutoff: int = ALPHA_CUTOFF) -> bytes:
    """RGBA frames -> a looping GIF with transparency. Shared by every renderer.

    `disposal=1` ("leave the previous frame") rather than 2 ("restore to
    background"). It is what lets the optimiser write only the changed
    rectangle -- 1.04 MB against 1.72 MB -- and it is safe here because the
    TRANSPARENT REGION IS IDENTICAL IN EVERY FRAME: the art is composited onto
    an opaque card face, so only the outer rim is ever transparent. If a
    renderer ever produces frames whose transparent area moves, this has to go
    back to 2 or the holes will ghost.
    """
    import numpy as np

    box = alpha_bbox(frames, cutoff)
    frames = [f.crop(box) for f in frames]

    transparent = colors                      # one past the last colour
    palette = _global_palette(frames, colors)
    reference = Image.new("P", (1, 1))
    reference.putpalette(palette)

    out = []
    for frame in frames:
        alpha = np.asarray(frame)[..., 3]
        indexed = np.array(_on_black(frame).quantize(palette=reference, dither=Image.NONE))
        indexed[indexed >= transparent] = transparent - 1   # nothing may collide
        indexed[alpha < cutoff] = transparent
        page = Image.fromarray(indexed, "P")
        page.putpalette(palette)
        page.info["transparency"] = transparent
        out.append(page)

    buf = io.BytesIO()
    out[0].save(buf, format="GIF", save_all=True, append_images=out[1:],
                duration=round(1000 / fps), loop=0,
                transparency=transparent, disposal=1, optimize=True)
    return buf.getvalue()


def render_gif(card: dict, art_bytes: bytes, duration=4.0, fps=15) -> bytes:
    """The card as a looping GIF. Only meaningful when `is_animated(card)`.

    The face is drawn once; each frame differs only inside the art box, which is
    what keeps the file small -- GIF's frame differencing has very little to
    encode outside a 275x275 region.
    """
    face = _base_face(card)
    primary, secondary = ef.colors_for_card(card)
    with _temp(art_bytes, ".exr") as path:
        art_frames = ef.frames(path, primary, secondary, duration=duration, fps=fps,
                               departure=ef.departure_for_card(card))

    pos = (round(L.ART[0]), round(L.ART[1]))
    frames = []
    for art in art_frames:
        frame = face.copy()
        frame.alpha_composite(art, pos)
        frames.append(frame)
    # Every card wears the holographic material in-game
    # (scenes/cards/base_card_material.tres, `_enableHolographic = true`).
    # It is most of what a colourless catalyst looks like: white art and a white
    # border, coloured only by this.
    return to_gif(holo.apply_all(frames), fps=fps)


def render_png(card: dict, art_bytes: bytes | None) -> bytes:
    """The card as a PNG, cropped to itself.

    PNG carries full alpha, so a still has never needed flattening -- but it did
    carry ~63px of empty canvas above and below, which Discord scaled down along
    with the card. Cropping is done HERE rather than in render_still, because
    deck_render lays its grid out from the full CARD_W x CARD_H box.

    The holographic sheen is applied HERE for the same reason: a deck grid draws
    at 200px, where the effect is invisible, and a 110-card deck would pay for it
    110 times over to show nothing.
    """
    still = holo.apply(render_still(card, art_bytes))
    buf = io.BytesIO()
    still.crop(alpha_bbox([still])).save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fetching art
# ---------------------------------------------------------------------------

# Eigenfunction art and flat art live in different Storage buckets, keyed by the
# card's `image` extension. Mirrors ImageCache.eigenfunction_name_for_image().
EXR_BUCKET = "eigenfunctions"
PNG_BUCKET = "cardimages"


def art_bucket(card: dict) -> str:
    return EXR_BUCKET if is_animated(card) else PNG_BUCKET


def download_art(bucket: str, filename: str) -> bytes:
    from supabase_client import supabase
    return supabase.storage.from_(bucket).download(filename)


def fetch_art(card: dict) -> bytes | None:
    """A card's art, from the on-disk cache when possible.

    Returns None when the card has no image set. A genuine download failure
    raises rather than rendering a card with a blank middle.
    """
    name = card.get("image")
    if not name:
        return None
    return art_cache.fetch_art_cached(art_bucket(card), name, download_art)


def render(card: dict, animate: bool = True, duration=4.0, fps=15):
    """Render a card, returning (bytes, file_extension).

    Animated only when the card carries eigenfunction art AND `animate` is set.
    A flat-PNG card has nothing to animate, so wrapping it in a GIF would just
    be a larger file showing the same thing.

    Only the ANIMATED result is cached: a still is ~0.04s and not worth the
    bookkeeping, while a GIF is 1.3-2.8s. The key covers the card's rendered
    fields, its art bytes and the renderer version, so an edit or a regenerated
    image invalidates it -- unlike the old renderer, which keyed on name alone
    and served stale images after any change.
    """
    art = fetch_art(card)
    if animate and is_animated(card) and art:
        key = art_cache.render_key(card, art, "card", duration=duration, fps=fps)
        hit = art_cache.get_render(key, "gif")
        if hit is not None:
            return hit, "gif"
        data = render_gif(card, art, duration=duration, fps=fps)
        art_cache.put_render(key, "gif", data)
        return data, "gif"
    return render_png(card, art), "png"
