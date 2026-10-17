"""Render an Azoth card face to a PNG or an animated GIF.

Reproduces `scenes/cards/card.tscn` from the azoth repo closely enough for
Discord. Deliberately NOT reproduced:

  * `base_card_shader.gdshader` -- tilt, specular, drop shadow, holographic
    sheen. All are responses to being hovered or moved in-game.
  * Enhancements and attributes. Both are applied during a run and never present
    on a card as authored.

Generated on the face, as the game generates them (content never writes them):
a weapon's damage badge and a Magnify badge hanging off the card's left edge
(`_with_badges`), and a weapon's escalate leading its text (`weapons.lead`).

Three art paths, because the content has three, checked in the game's order
(`ImageCache.has_animated_art`): procedural art under `image_data.art` (made in
the game's Codex; computed, never downloaded), then a card whose `image` ends in
`.exr` (eigenfunction art), both of which animate; anything else is a flat PNG
and does not.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from azoth_logic import art_cache
from azoth_logic import holo
from azoth_logic import card_layout as L
from azoth_logic import eigenfunction_art as ef
from azoth_logic import inline_icons
from azoth_logic import rich_text
from azoth_logic import weapons

ASSET_ROOT = Path(__file__).resolve().parent.parent / "assets"
BORDER_DIR = ASSET_ROOT / "card_art" / "borders"
FONT_PATH = ASSET_ROOT / "fonts" / L.FONT_FILE

# Discord's dark-theme message background, (49, 51, 56). REMOVED 2026-08-28 and
# deliberately not replaced. Single cards carry real transparency (see to_gif),
# and the multi-item sheets moved to black (`deck_render.SHEET_BG`) -- matching
# one theme's grey meant missing on the other three.

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


BORDER_BLOBS = ASSET_ROOT / "card_art" / "border_blobs.json"
_blob_cache = None


def _blobs():
    global _blob_cache
    if _blob_cache is None:
        import json
        if not BORDER_BLOBS.is_file():
            raise FileNotFoundError(
                f"{BORDER_BLOBS} is missing. Run: python -m tools.sync_assets --azoth <path>")
        _blob_cache = json.loads(BORDER_BLOBS.read_text())["border"]
    return _blob_cache


def _blob_distance(px, py, hide_badge_lobe: bool):
    """Signed distance to the border blobs, in border pixels, negative inside.

    `card_border_blobs.gdshaderinc`'s `border_blobs`: each blob a log-sum-exp
    smooth maximum of its edge half-planes, the union their minimum. Blob 1 is
    the small lobe under the top-left corner, which a corner badge drops.
    """
    import numpy as np
    blobs = _blobs()["blobs"]
    d = None
    for index, blob in enumerate(blobs):
        if index == 1 and hide_badge_lobe:
            continue
        planes = [px * a + py * b - o for a, b, o in blob["planes"]]
        m = np.maximum.reduce(planes)
        k = blob["k"]
        v = m + k * np.log(sum(np.exp((pl - m) / k) for pl in planes))
        d = v if d is None else np.minimum(d, v)
    return d


def _coverage(d):
    """`blob_coverage`: clamp(0.5 - d / fwidth(d)), fwidth = |ddx| + |ddy|."""
    import numpy as np
    gy, gx = np.gradient(d)
    return np.clip(0.5 - d / np.maximum(np.abs(gx) + np.abs(gy), 1e-4), 0.0, 1.0)


def _border(card: dict, hide_badge_lobe: bool = False) -> Image.Image:
    """The card body and its element blobs, RGB, over the PreviouslyImage box.

    Port of `card_border_dim.gdshader` (the game stopped loading per-element
    border PNGs; the blobs are signed distance fields fitted to the old shape):

        rgb = mix(dim_color, base_color, base_coverage)
        rgb = mix(rgb, split_color, split_coverage)    # split cards only

    A split's second element sits on the left-right MIRROR of the blobs, so its
    lobes are the base's reflected; its badge lobe is never hidden. A colourless
    card wears the rainbow, frozen at TIME = 0. The blobs' breathing and edge
    wave are motion and are not drawn: at rest they sit on the fitted shape.
    """
    import numpy as np
    bw, bh = _blobs()["size"]
    x0, y0, w, h = L.BORDER
    W, H = round(w), round(h)
    # Pixel centres, in border space (the box is the border's 600x816).
    xs = (np.arange(W, dtype=np.float64) + 0.5) * (bw / w)
    ys = (np.arange(H, dtype=np.float64) + 0.5) * (bh / h)
    px, py = np.meshgrid(xs, ys)

    base_cov = _coverage(_blob_distance(px, py, hide_badge_lobe))[..., None]
    if L.is_colorless(card):
        u, v = px / bw, py / bh
        base_col = 0.85 + 0.2 * np.cos(np.dstack([3.0 * u + 2.0, 3.0 * v + 2.0, 3.0 * u + 4.0]))
    else:
        base_col = np.asarray(L.element_color(card.get("element")), dtype=np.float64) / 255.0
    dim = np.asarray(L.FACE_COLOR, dtype=np.float64) / 255.0
    rgb = dim * (1.0 - base_cov) + base_col * base_cov

    face = L.split_face(card)
    if face is not None and face[0]:
        split_cov = _coverage(_blob_distance(bw - px, py, False))[..., None]
        split_col = np.asarray(L.element_color(face[0]), dtype=np.float64) / 255.0
        rgb = rgb * (1.0 - split_cov) + split_col * split_cov

    rgb = (np.clip(rgb, 0.0, 1.0) * 255.0).round().astype("uint8")
    alpha = np.full((H, W), 255, dtype="uint8")
    return Image.fromarray(np.dstack([rgb, alpha]), "RGBA")


def _base_face(card: dict) -> Image.Image:
    """Everything except the art: background, border, valence, name, subtype, text.

    Drawn once and reused across animation frames -- only the art moves.
    """
    canvas = Image.new("RGBA", (L.CARD_W, L.CARD_H), (0, 0, 0, 0))

    # The Background's alpha is the card's silhouette (the shader cuts it in
    # game); the border paints the body and blobs inside it.
    bg = _layer(L.BACKGROUND_FILE, L.BACKGROUND)
    border = _border(card, hide_badge_lobe=has_badge(card))
    bx, by = round(L.BORDER[0]), round(L.BORDER[1])
    silhouette = Image.new("L", border.size, 0)
    silhouette.paste(bg.getchannel("A"),
                     (round(L.BACKGROUND[0]) - bx, round(L.BACKGROUND[1]) - by))
    border.putalpha(silhouette)
    canvas.alpha_composite(border, (bx, by))

    draw = ImageDraw.Draw(canvas)
    colour = L.element_color(card.get("element"))

    # --- Valence, in the corner wedge the border art provides ---------------
    # A colourless card hides its valence corner (BaseCard._apply_colorless_visuals).
    valence = card.get("valence")
    if valence is not None and not L.is_colorless(card):
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
    # Centred in TextLabel's region on its ink (rich_text.typeset). A weapon's
    # escalate leads it, generated from the card's numbers.
    text = weapons.text_with_lead(card, str(card.get("text") or ""))
    if text:
        rich_text.draw_typeset(canvas, rich_text.typeset(text, L.text_region()), L.TEXT_COLOR)
    return canvas


# ---------------------------------------------------------------------------
# Corner badges
# ---------------------------------------------------------------------------

def magnify_amount(card: dict) -> int:
    """The extra copies the card's Magnify properties grant (Utils.get_magnify_amount).
    A number, or the card's valence for `{valence}` / `{self.valence}`; any
    other run-time amount has nothing to resolve against and counts 0."""
    total = 0
    for prop in card.get("properties") or []:
        if not isinstance(prop, dict) or prop.get("name") != "Magnify":
            continue
        amount = prop.get("amount", 1)
        if isinstance(amount, str) and amount.strip() in ("{valence}", "{self.valence}"):
            amount = card.get("valence")
        try:
            total += int(amount)
        except (TypeError, ValueError):
            continue
    return max(total, 0)


def has_badge(card: dict) -> bool:
    """A weapon's damage, a Magnify, or both (BaseCard._badge_showing)."""
    return weapons.is_weapon(card) or magnify_amount(card) > 0


def _damage_badge_height() -> float:
    """BaseCard._damage_badge_height: DAMAGE_BADGE_SCALE x the escalate pointer
    a lone-icon text draws."""
    font_size = round(rich_text.PREFERRED_SIZE * inline_icons.LONE_ICON_SCALE)
    return (inline_icons.ESCALATE_HEIGHT_FRACTION * inline_icons.SYMBOL_INK_EM
            * font_size * L.DAMAGE_BADGE_SCALE)


def _with_badges(card: dict, frame: Image.Image) -> Image.Image:
    """`frame` with the card's corner badges, on a canvas widened on the left
    by `BADGE_PAD` so they can hang past the card's edge. Unchanged for a card
    with neither.

    BaseCard._push_badge_uniforms: the damage pointer, in the element's colour,
    takes the Damage_Badge slot (its left edge, centred on its height); the
    Magnify glyph is fitted into the Magnify_Badge rect, and moved down below
    the pointer when both show. The damage is drawn last, on top.
    """
    if not has_badge(card):
        return frame
    pad = L.BADGE_PAD
    out = Image.new("RGBA", (frame.width + pad, frame.height), (0, 0, 0, 0))
    out.alpha_composite(frame, (pad, 0))

    pointer = None
    pointer_at = (0.0, 0.0)
    damage = weapons.damage(card)
    if damage is not None:
        h = _damage_badge_height()
        pointer = inline_icons.pointer(str(damage), "", h, fill=L.element_color(card.get("element")))
        dx, dy, _, dh = L.DAMAGE_BADGE
        pointer_at = (dx, dy + (dh - h) / 2)

    amount = magnify_amount(card)
    if amount > 0:
        glyph = Image.open(inline_icons.SYMBOL_DIR / _magnify_file(amount)).convert("RGBA")
        mx, my, mw, mh = L.MAGNIFY_BADGE
        fit = min(mw / glyph.width, mh / glyph.height)
        gw, gh = glyph.width * fit, glyph.height * fit
        gx, gy = mx + (mw - gw) / 2, my + (mh - gh) / 2
        if pointer is not None:
            cx, cy, r = L.BADGE_CIRCLE_IN_ICON
            body_top = gy + (cy - r) * gw
            gy += pointer_at[1] + pointer.image.height + L.MAGNIFY_BELOW_DAMAGE_GAP - body_top
        out.alpha_composite(glyph.resize((max(1, round(gw)), max(1, round(gh))), Image.LANCZOS),
                            (round(gx) + pad, round(gy)))
    if pointer is not None:
        out.alpha_composite(pointer.image, (round(pointer_at[0]) + pad, round(pointer_at[1])))
    return out


def _magnify_file(amount: int) -> str:
    """Utils.magnify_token: numbered glyphs to 9, then the big one."""
    return "magnify_big.png" if amount > 9 else f"magnify_{amount}.png"


def compose(card: dict, face: Image.Image, art: Image.Image | None) -> Image.Image:
    """One finished frame: `face` (from `_base_face`) with `art` in its box and
    the corner badges. Every renderer of a card face goes through here."""
    frame = face.copy()
    if art is not None:
        frame.alpha_composite(art, (round(L.ART[0]), round(L.ART[1])))
    return _with_badges(card, frame)


def _draw_centered_line(draw, text, font, box, fill, outline=0, outline_color=(0, 0, 0)):
    x, y, w, h = box
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    px = x + (w - (right - left)) / 2 - left
    py = y + (h - (bottom - top)) / 2 - top
    draw.text((px, py), text, font=font, fill=fill,
              stroke_width=outline, stroke_fill=outline_color)


def is_animated(card: dict) -> bool:
    """Procedural art or eigenfunction (.exr) art. See the module docstring."""
    return ef.is_animated(card)


def _art_still(card, art_bytes):
    """The art as one frame, or None when there is nothing to draw."""
    primary, secondary = ef.colors_for_card(card)
    if is_animated(card):
        return ef.item_still(card, art_bytes, primary, secondary)
    if not art_bytes:
        return None
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
    """The card as a single RGBA frame. Wider than the viewport, on the left,
    when it carries a corner badge (`_with_badges`)."""
    return compose(card, _base_face(card), _art_still(card, art_bytes))


def body_bbox(frames, cutoff: int = ALPHA_CUTOFF):
    """`alpha_bbox` of the card itself, leaving out a corner badge hanging past
    its left edge (`_with_badges`). Sheets scale a tile by this, so a badged
    card is drawn the same size as its neighbours and its badge overhangs."""
    full = alpha_bbox(frames, cutoff)
    if frames[0].width != L.CARD_W + L.BADGE_PAD:
        return full
    inner = alpha_bbox([f.crop((L.BADGE_PAD, 0, f.width, f.height)) for f in frames], cutoff)
    return (inner[0] + L.BADGE_PAD, full[1], inner[2] + L.BADGE_PAD, full[3])


def fit_width(frames, width: int):
    """Frames cropped to themselves and scaled so the CARD is `width` wide.
    Returns (frames, overhang): `overhang` is how far a badge reaches left of
    the card, in the scaled pixels (0 for most)."""
    full, body = alpha_bbox(frames), body_bbox(frames)
    box = (min(full[0], body[0]), full[1], body[2], full[3])
    scale = width / max(body[2] - body[0], 1)
    size = (max(1, round((box[2] - box[0]) * scale)), max(1, round((box[3] - box[1]) * scale)))
    scaled = [f.crop(box).resize(size, Image.LANCZOS) for f in frames]
    return scaled, size[0] - width


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
    """RGB for quantisation. Black: pixels below the cutoff are replaced by the
    transparent index anyway, so the matte only has to be a colour that does not
    pull the palette toward a background nobody sees."""
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


def render_gif(card: dict, art_bytes: bytes | None, duration=4.0, fps=15) -> bytes:
    """The card as a looping GIF. Only meaningful when `is_animated(card)`.

    The face is drawn once; each frame differs only inside the art box, which is
    what keeps the file small -- GIF's frame differencing has very little to
    encode outside a 275x275 region.
    """
    face = _base_face(card)
    primary, secondary = ef.colors_for_card(card)
    art_frames = ef.item_frames(card, art_bytes, primary, secondary,
                                duration=duration, fps=fps)

    frames = [compose(card, face, art) for art in art_frames]
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
# Procedural art is in neither: it is computed from the row.
EXR_BUCKET = "eigenfunctions"
PNG_BUCKET = "cardimages"


def art_bucket(card: dict) -> str:
    return EXR_BUCKET if ef.is_exr(card) else PNG_BUCKET


def download_art(bucket: str, filename: str) -> bytes:
    from supabase_client import supabase
    return supabase.storage.from_(bucket).download(filename)


def fetch_art(card: dict) -> bytes | None:
    """A card's art, from the on-disk cache when possible.

    Returns None when the card has no image set. A genuine download failure
    raises rather than rendering a card with a blank middle.
    """
    if not ef.needs_download(card):
        return None
    return art_cache.fetch_art_cached(art_bucket(card), card["image"], download_art)


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
    # Procedural art needs no bytes; an .exr does.
    if animate and is_animated(card) and (art or ef.procedural_art.has_art(card)):
        key = art_cache.render_key(card, art, "card", duration=duration, fps=fps)
        hit = art_cache.get_render(key, "gif")
        if hit is not None:
            return hit, "gif"
        data = render_gif(card, art, duration=duration, fps=fps)
        art_cache.put_render(key, "gif", data)
        return data, "gif"
    return render_png(card, art), "png"
