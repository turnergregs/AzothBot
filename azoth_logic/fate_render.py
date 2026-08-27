"""Render aspect and rite cards.

Companion to `card_render.py`, which handles ordinary cards. The three differ
enough to be worth separating:

| | Card | Aspect | Rite |
|---|---|---|---|
| Background | Static PNG + element border | One shader pattern, tinted per aspect | One of four shader patterns |
| Art | `.exr` or PNG, 275x275 | `.exr`, 210x210 | **None** -- the Image node is hidden |
| Valence | Yes, plus a split face | No | No |
| Colours | Element-driven | `image_data` primary/secondary | Fixed in the scene |

**"Rite" is what the database still calls an "event".** New code says rite;
`content_type`, the table and the Storage bucket stay `event`.

Aspects animate, since their art is eigenfunction `.exr`. Rites carry flat PNG
art and render static -- the same rule `card_render` applies.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw

from azoth_logic import art_cache, card_render, eigenfunction_art as ef, fate_layout as F, rich_text
from azoth_logic.card_layout import CARD_W, CARD_H

BACKGROUND_DIR = Path(__file__).resolve().parent.parent / "assets" / "card_art" / "backgrounds"

_bg_cache: dict = {}


def _background(file_name: str, box, tint=None) -> Image.Image:
    """Load a vendored background, scaled to its box, optionally tinted.

    The aspect background is exported WHITE precisely so it can be multiplied by
    each aspect's own colour here -- exporting it pre-tinted would bake one
    aspect's hue into all of them.
    """
    key = (file_name, box, tint)
    if key in _bg_cache:
        return _bg_cache[key]
    img = Image.open(BACKGROUND_DIR / file_name).convert("RGBA")
    _, _, w, h = box
    img = img.resize((round(w), round(h)), Image.LANCZOS)
    if tint is not None:
        import numpy as np
        a = np.asarray(img, dtype=np.float32) / 255.0
        a[..., :3] *= np.asarray(tint, dtype=np.float32) / 255.0
        img = Image.fromarray((a * 255.0).round().astype("uint8"), "RGBA")
    _bg_cache[key] = img
    return img


def _draw_text_block(canvas, text, font, box, color, stroke=0, stroke_color=(0, 0, 0)):
    """Wrapped, centred text, vertically centred on its box.

    Both scenes set the label to fit_content with centred alignment, so the block
    grows about the box's middle rather than downward from its top.
    """
    if not text:
        return
    bx, by, bw, bh = box
    lines = rich_text.layout(str(text), font, F.wrap_width(bw))
    height = rich_text.measure(lines, font, 0)
    rich_text.draw_centered(canvas, lines, font, (bx, by + (bh - height) / 2, bw, height),
                            color, stroke=stroke, stroke_color=stroke_color)


# ---------------------------------------------------------------------------
# Aspects
# ---------------------------------------------------------------------------

def is_animated(item: dict) -> bool:
    return str(item.get("image") or "").lower().endswith(".exr")


def _aspect_face(aspect: dict) -> Image.Image:
    primary, secondary = F.aspect_colors(aspect)
    canvas = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))

    bg = _background(F.ASPECT_BACKGROUND_FILE, F.ASPECT_BACKGROUND, tint=primary)
    canvas.alpha_composite(bg, (round(F.ASPECT_BACKGROUND[0]), round(F.ASPECT_BACKGROUND[1])))

    # Name and type. The name's outline is its own colour, so it reads as weight.
    _draw_text_block(canvas, aspect.get("name"), card_render._font(F.ASPECT_NAME_SIZE),
                     F.ASPECT_NAME, secondary,
                     stroke=F.NAME_OUTLINE, stroke_color=secondary)
    _draw_text_block(canvas, aspect.get("text"), card_render._font(F.ASPECT_TEXT_SIZE),
                     F.ASPECT_TEXT, F.ASPECT_TEXT_COLOR)
    return canvas


def aspect_art_colors(aspect: dict):
    """(base zone, accent zone) for an aspect's eigenfunction art.

    Reversed against the name label: the accent takes `primary_color` and the
    base takes `secondary_color`. See fate_layout.aspect_colors.
    """
    primary, secondary = F.aspect_colors(aspect)
    return secondary, primary


def render_aspect(aspect: dict, art_bytes: bytes | None, animate: bool = True,
                  duration=4.0, fps=15):
    """Returns (bytes, extension)."""
    face = _aspect_face(aspect)
    pos = (round(F.ASPECT_ART[0]), round(F.ASPECT_ART[1]))
    size = (round(F.ASPECT_ART[2]), round(F.ASPECT_ART[3]))
    base, accent = aspect_art_colors(aspect)

    if art_bytes and animate and is_animated(aspect):
        with card_render._temp(art_bytes, ".exr") as path:
            frames = ef.frames(path, base, accent, duration=duration, fps=fps,
                               departure=ef.departure_for_card(aspect))
        flat = []
        for art in frames:
            frame = face.copy()
            frame.alpha_composite(art.resize(size, Image.LANCZOS), pos)
            flat.append(Image.alpha_composite(
                Image.new("RGBA", frame.size, card_render.DISCORD_BG + (255,)), frame
            ).convert("P", palette=Image.ADAPTIVE, colors=128))
        buf = io.BytesIO()
        flat[0].save(buf, format="GIF", save_all=True, append_images=flat[1:],
                     duration=round(1000 / fps), loop=0, optimize=True)
        return buf.getvalue(), "gif"

    if art_bytes:
        if is_animated(aspect):
            with card_render._temp(art_bytes, ".exr") as path:
                art = ef.still(path, base, accent)
        else:
            art = Image.open(io.BytesIO(art_bytes)).convert("RGBA")
        face.alpha_composite(art.resize(size, Image.LANCZOS), pos)

    buf = io.BytesIO()
    face.save(buf, format="PNG")
    return buf.getvalue(), "png"


# ---------------------------------------------------------------------------
# Rites
# ---------------------------------------------------------------------------

# Shader defaults, for a rite that overrides only SOME of the three colours.
# reactant_card.gdshader's `background_color` default, and the act-1 palette its
# colour chain falls into.
_SHADER_DEFAULT_BG = (200, 20, 20)
_SHADER_DEFAULT_PRIMARY = (81, 158, 34)
_SHADER_DEFAULT_SECONDARY = (100, 143, 50)


def _recolored_rite_background(rite: dict, overrides) -> Image.Image:
    """Rebuild a rite background in the rite's own palette.

    Inverts reactant_card.gdshader from the channel-encoded mask:

        pattern^2 = G + B
        cp        = G / (G + B)
        out       = background * (1 - pattern^2)
                  + mix(primary, secondary, cp) * pattern^2

    Only the colours the rite actually sets are overridden; the rest fall back to
    the shader's own defaults, matching how EventVisuals treats a partial dict --
    a row that sets just `background_color` keeps the authored palette.
    """
    import numpy as np

    bg, primary, secondary = overrides
    bg = np.asarray(bg if bg else _SHADER_DEFAULT_BG, np.float32)
    primary = np.asarray(primary if primary else _SHADER_DEFAULT_PRIMARY, np.float32)
    secondary = np.asarray(secondary if secondary else _SHADER_DEFAULT_SECONDARY, np.float32)

    mask = Image.open(BACKGROUND_DIR / F.rite_mask_file(rite.get("name"))).convert("RGBA")
    _, _, w, h = F.RITE_BACKGROUND
    mask = mask.resize((round(w), round(h)), Image.LANCZOS)
    m = np.asarray(mask, np.float32) / 255.0

    pattern = np.clip(m[..., 1] + m[..., 2], 0.0, 1.0)[..., None]
    cp = np.divide(m[..., 1], np.maximum(m[..., 1] + m[..., 2], 1e-6))[..., None]

    col = primary * (1.0 - cp) + secondary * cp
    rgb = bg * (1.0 - pattern) + col * pattern
    out = np.dstack([np.clip(rgb, 0, 255), m[..., 3] * 255.0])
    return Image.fromarray(out.round().astype("uint8"), "RGBA")


def _rite_face(rite: dict) -> Image.Image:
    canvas = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    overrides = F.rite_colors(rite)
    if overrides is None:
        # No palette of its own: the baked export already carries the material's
        # authored colours exactly.
        bg = _background(F.rite_background_file(rite.get("name")), F.RITE_BACKGROUND)
    else:
        bg = _recolored_rite_background(rite, overrides)
    canvas.alpha_composite(bg, (round(F.RITE_BACKGROUND[0]), round(F.RITE_BACKGROUND[1])))
    return canvas


def _rite_text(canvas, rite: dict) -> None:
    # A rite that authored a palette foregrounds its NAME and RULES TEXT in it,
    # per event_card.gd::set_event_text_color(). One that did not keeps the
    # scene's own blue name and orange text -- 23 of the 44 live rites.
    foreground = F.rite_text_color(rite)
    name_color = foreground or F.RITE_NAME_COLOR
    name_outline = foreground or F.RITE_NAME_OUTLINE_COLOR
    text_color = foreground or F.RITE_TEXT_COLOR

    _draw_text_block(canvas, rite.get("name"), card_render._font(F.RITE_NAME_SIZE),
                     F.RITE_NAME, name_color,
                     stroke=F.NAME_OUTLINE, stroke_color=name_outline)
    _draw_text_block(canvas, rite.get("text"), card_render._font(F.RITE_TEXT_SIZE),
                     F.RITE_TEXT, text_color)


def _rite_background_frames(rite: dict, overrides):
    """Animated background frames for a rite, or None if that variant is static.

    reactant_card.gdshader modulates its pattern amplitudes from TIME, so the
    blobs breathe. Frames are a vendored WebP of the channel-encoded mask,
    recoloured here exactly as the still path does.

    PING-PONG rather than a cross-fade. The shader's noise is not periodic, so
    there is no natural loop -- and unlike the eigenfunction art there is no
    smooth field to blend in: the pattern has crisp edges, so cross-fading two
    frames would ghost. Playing the sequence forward then backward loops exactly,
    and reads naturally because the motion is amplitude modulation rather than
    travel.
    """
    import numpy as np

    anim = F.rite_mask_anim_file(rite.get("name"))
    if anim is None:
        return None
    path = BACKGROUND_DIR / anim
    if not path.is_file():
        return None

    bg, primary, secondary = overrides if overrides else (None, None, None)
    bg = np.asarray(bg if bg else _SHADER_DEFAULT_BG, np.float32)
    primary = np.asarray(primary if primary else _SHADER_DEFAULT_PRIMARY, np.float32)
    secondary = np.asarray(secondary if secondary else _SHADER_DEFAULT_SECONDARY, np.float32)

    _, _, w, h = F.RITE_BACKGROUND
    size = (round(w), round(h))
    src = Image.open(path)
    out = []
    for i in range(getattr(src, "n_frames", 1)):
        src.seek(i)
        m = np.asarray(src.convert("RGBA").resize(size, Image.LANCZOS), np.float32) / 255.0
        pattern = np.clip(m[..., 1] + m[..., 2], 0.0, 1.0)[..., None]
        cp = np.divide(m[..., 1], np.maximum(m[..., 1] + m[..., 2], 1e-6))[..., None]
        col = primary * (1.0 - cp) + secondary * cp
        rgb = bg * (1.0 - pattern) + col * pattern
        out.append(Image.fromarray(
            np.dstack([np.clip(rgb, 0, 255), m[..., 3] * 255.0]).round().astype("uint8"), "RGBA"))

    # Ping-pong: forward, then back without repeating either endpoint.
    return out + out[-2:0:-1]


def render_rite_gif(rite: dict, fps: int = 15) -> bytes | None:
    """A rite as a looping GIF, or None when its background does not animate."""
    frames = _rite_background_frames(rite, F.rite_colors(rite))
    if not frames:
        return None
    flat = []
    for bg in frames:
        face = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
        face.alpha_composite(bg, (round(F.RITE_BACKGROUND[0]), round(F.RITE_BACKGROUND[1])))
        _rite_text(face, rite)
        flat.append(Image.alpha_composite(
            Image.new("RGBA", face.size, card_render.DISCORD_BG + (255,)), face
        ).convert("P", palette=Image.ADAPTIVE, colors=128))
    buf = io.BytesIO()
    flat[0].save(buf, format="GIF", save_all=True, append_images=flat[1:],
                 duration=round(1000 / fps), loop=0, optimize=True)
    return buf.getvalue()


def render_rite(rite: dict, art_bytes: bytes | None = None):
    """Returns (bytes, extension). Always static, and never draws art.

    `event_card.tscn` ships the Image node with `visible = false`: a rite's
    visual IS its background pattern, and the `image` column feeds the draft
    thumbnail rather than the card face. Drawing it puts a blob over the middle
    of the card that the game never shows. `art_bytes` is accepted and ignored
    so the call shape matches the other renderers.
    """
    face = _rite_face(rite)
    _rite_text(face, rite)
    buf = io.BytesIO()
    face.save(buf, format="PNG")
    return buf.getvalue(), "png"


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch_art(item: dict, bucket: str) -> bytes | None:
    name = item.get("image")
    if not name:
        return None
    source = card_render.EXR_BUCKET if is_animated(item) else bucket
    return art_cache.fetch_art_cached(source, name, card_render.download_art)


def render(item: dict, kind: str):
    """Render an 'aspect' or a 'rite'. Returns (bytes, extension)."""
    if kind == "aspect":
        art = fetch_art(item, "aspectimages")
        key = art_cache.render_key(item, art, "aspect")
        hit = art_cache.get_render(key, "gif")
        if hit is not None:
            return hit, "gif"
        data, ext = render_aspect(item, art)
        if ext == "gif":
            art_cache.put_render(key, "gif", data)
        return data, ext
    if kind == "rite":
        # No art fetch: the rite card face never shows it (see render_rite).
        key = art_cache.render_key(item, None, "rite")
        hit = art_cache.get_render(key, "gif")
        if hit is not None:
            return hit, "gif"
        gif = render_rite_gif(item)
        if gif:
            art_cache.put_render(key, "gif", gif)
            return gif, "gif"
        return render_rite(item)
    raise ValueError(f"unknown fate kind: {kind}")
