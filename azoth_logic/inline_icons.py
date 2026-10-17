"""How an inline icon in card text is drawn: the port of the game's InlineIcons.

Since 2026-09-27 the game sets card text with its own typesetter rather than a
RichTextLabel (the azoth repo's `docs/TYPESETTER.md`,
`scripts/helpers/inline_icons.gd`, `scripts/helpers/typesetter.gd`). Three
things changed about icons, and a renderer drawing the PNGs as they stand gets
all three wrong:

  * **Icons are laid out by their ink, not their box.** The numbered families
    and the hexagons leave the bottom ~20% of their PNG empty. An icon occupies
    its ink width in the line, and its ANCHOR (its own ink, or for anything on
    the shared hexagon canvas, the plain hexagon's) is centred on the capitals.
  * **Numbers are not baked art any more.** A recipe turns a file name back into
    a blank base plus a label set in the card's own font (`[4life]` is
    `life.png` + "4"). Bonus and mult go further: a rounded TAG drawn in code
    that widens to fit "x4" / "x1.5" / "16". A file with no recipe still draws
    as its PNG.
  * **Weapons have pointers.** `[20damage]` is a white pointer with "20", and
    `[escalate3]` a gold one with a triangle and "3". No such files exist: the
    recipe reads the number out of the name. The damage pointer is also the
    card's corner badge, in the element's colour (`pointer()` below).

Symbols (recipes and the hexagon family) share one height: their anchor is
`SYMBOL_INK_EM` x the font size. Other icons keep the height their token is
authored at (`Utils.icon_height_for`), scaled with the font size.

Every constant here is the game's, by the same name. Keep them in step by hand.
Shapes and labels are drawn at `SUPERSAMPLE` x and scaled down, which is how
they get the antialiasing PIL's polygon fill does not have.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSET_ROOT = Path(__file__).resolve().parent.parent / "assets"
SYMBOL_DIR = ASSET_ROOT / "card_art" / "symbols"
FONT_PATH = ASSET_ROOT / "fonts" / "Aldrich-Regular.ttf"

# --- Typesetter ----------------------------------------------------------------
# What every [img] height is authored against (TypesetLabel.base_font_size).
BASE_FONT_SIZE = 40
# A symbol's anchor height, as a multiple of the font size.
SYMBOL_INK_EM = 1.25
# A text that is one icon alone is set at the preferred size times this.
LONE_ICON_SCALE = 1.33
# Where an icon's anchor centre sits, as a fraction of the cap height.
ICON_CENTRE = 0.5

# --- Utils.icon_height_for ---------------------------------------------------
# The height a token's [img] tag asks for: 1.35x for these families, beside
# text of Utils.replace_icon_from_dict's default size, 50.
REPLACE_FONT_SIZE = 50
LARGE_ICON_TOKEN_PARTS = ("mult", "bonus", "life", "magnify")
LARGE_ICON_FACTOR = 1.35

# --- InlineIcons ---------------------------------------------------------------
LABEL_CAP_FRACTION = 0.45
LABEL_WIDTH_FRACTION = 0.62
LABEL_RAISE = 0.02
LABEL_STROKE_FRACTION = 0.1
LABEL_DARK = (22, 22, 22)           # Color(0.08627451, ...)

TAG_HEIGHT_FRACTION = 0.8
TAG_LABEL_CAP_FRACTION = 0.5
TAG_PADDING_FRACTION = 0.28
TAG_RADIUS_FRACTION = 0.3

ESCALATE_HEIGHT_FRACTION = TAG_HEIGHT_FRACTION
ESCALATE_DAMAGE_CAP = TAG_LABEL_CAP_FRACTION
ESCALATE_AMOUNT_CAP = TAG_LABEL_CAP_FRACTION
ESCALATE_ARROW_HEIGHT = 0.35
ESCALATE_SLANT = 0.0
ESCALATE_PADDING = 0.2
ESCALATE_POINT = 0.36
ESCALATE_CORNER = 0.1
ESCALATE_DAMAGE_COLOR = (255, 255, 255)
ESCALATE_DAMAGE_LABEL = LABEL_DARK
ESCALATE_AMOUNT_COLOR = (0xE3, 0xA3, 0x3A)
ESCALATE_AMOUNT_LABEL = (0x2A, 0x16, 0x06)

TAG = "tag"
ESCALATE = "escalate"

# file-name pattern -> how to draw it, first match wins (InlineIcons.RECIPES).
RECIPES = [
    (re.compile(r"^(\d+)damage\.png$"), {"shape": ESCALATE, "label": "{1}"}),
    (re.compile(r"^escalate(\d+)\.png$"), {"shape": ESCALATE, "label": "", "label2": "{1}"}),
    (re.compile(r"^generic_(\d+)\.png$"), {"base": "generic_empty.png", "label": "{1}"}),
    (re.compile(r"^(\d+)pt(\d+)bonus\.png$"), {"shape": TAG, "label": "x{1}.{2}"}),
    (re.compile(r"^(\d+)bonus\.png$"), {"shape": TAG, "label": "x{1}"}),
    (re.compile(r"^(\d+)mult\.png$"), {"shape": TAG, "label": "{1}"}),
    (re.compile(r"^(\d+)life_inv\.png$"), {"base": "life.png", "label": "{1}",
                                           "base_tint": (0, 0, 0), "label_color": (255, 255, 255)}),
    (re.compile(r"^(\d+)life\.png$"), {"base": "life.png", "label": "{1}"}),
    (re.compile(r"^(\d+)combo\.png$"), {"base": "combo.png", "label": "{1}"}),
]

# Files that are symbols with no label to set (InlineIcons.SYMBOL_FILES).
SYMBOL_FILES = re.compile(
    r"^(generic_\w+|not_(sol|blood|anima|set|sequence|group)\d*|not_\d+|non_\d+_valence"
    r"|\d+valence|(sol|blood|anima)(_\w+)?|(set|sequence|group|link)(_\w+)?)\.png$")

# Every file on the shared 240x340 hexagon canvas is sized and centred by this
# file's hexagon, so a pattern icon's badge hangs below instead of shrinking it.
HEX_ANCHOR = "generic_empty.png"

# The and / or connectors are set as bold words, not icons.
OPERATORS = {"and_icon.png": "&", "or_icon.png": "||"}

# No advance correction here, unlike card_layout.GODOT_ADVANCE_RATIO (2.6%,
# measured on the old RichTextLabel and still right for the name Label). The
# typesetter measures with Font.get_string_size, which agrees with PIL: run
# over every shipped card, aspect and rite, the typesetter port chose the
# game's size and breaks for 222 of 223 texts uncorrected, 204 at 1.026.

SUPERSAMPLE = 4

WEAPON_TOKEN = re.compile(r"\[(\d+damage|escalate\d+)\]")


@dataclass
class Icon:
    """One inline icon at one font size.

    `image` is the whole drawn picture. `ink` is its visible part and `anchor`
    the part centred on the capitals, both `(x, y, w, h)` in the image's pixels.
    A drawn shape (tag, pointer) is all three at once.
    """
    image: Image.Image
    ink: tuple
    anchor: tuple

    @property
    def ink_width(self) -> float:
        return self.ink[2]

    def reach(self, icon_mid: float):
        """(above, below) the baseline when its anchor centre sits `icon_mid`
        above it. The game's `_word_icon_extents`."""
        anchor_mid = self.anchor[1] + self.anchor[3] / 2
        return (icon_mid + anchor_mid - self.ink[1],
                self.ink[1] + self.ink[3] - anchor_mid - icon_mid)


# ---------------------------------------------------------------------------
# Fonts and metrics
# ---------------------------------------------------------------------------

@lru_cache(maxsize=64)
def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_PATH), max(1, int(size)))


@lru_cache(maxsize=64)
def cap_height(size: int) -> float:
    """The ink height of "H" -- Typesetter._metrics' x."""
    return float(-font(size).getbbox("H", anchor="ls")[1])


@lru_cache(maxsize=64)
def descender(size: int) -> float:
    """How far "g" reaches below the baseline -- Typesetter._metrics' y."""
    return float(font(size).getbbox("g", anchor="ls")[3])


def cap_ratio() -> float:
    return cap_height(100) / 100.0


def text_width(text: str, size: int) -> float:
    return font(size).getlength(text)


def icon_height_for(token: str) -> float:
    """The height a token's [img] tag carries (Utils.icon_height_for at 50)."""
    if any(part in token for part in LARGE_ICON_TOKEN_PARTS):
        return LARGE_ICON_FACTOR * REPLACE_FONT_SIZE
    return float(REPLACE_FONT_SIZE)


# ---------------------------------------------------------------------------
# Textures
# ---------------------------------------------------------------------------

@lru_cache(maxsize=512)
def _texture(file_name: str) -> Image.Image:
    path = SYMBOL_DIR / file_name
    if not path.is_file():
        raise FileNotFoundError(f"symbol {file_name} not vendored; run tools/sync_assets.py")
    return Image.open(path).convert("RGBA")


@lru_cache(maxsize=512)
def ink_rect(file_name: str) -> tuple:
    """The texture's visible pixels as fractions of its size (InlineIcons.ink_rect)."""
    tex = _texture(file_name)
    box = tex.getchannel("A").getbbox()
    if box is None:
        return (0.0, 0.0, 1.0, 1.0)
    w, h = tex.size
    return (box[0] / w, box[1] / h, (box[2] - box[0]) / w, (box[3] - box[1]) / h)


@lru_cache(maxsize=512)
def anchor_rect(file_name: str) -> tuple:
    """The hexagon's ink for anything on the hexagon canvas, else the file's own."""
    try:
        hexagon = _texture(HEX_ANCHOR)
    except FileNotFoundError:
        hexagon = None
    if hexagon is not None and _texture(file_name).size == hexagon.size:
        return ink_rect(HEX_ANCHOR)
    return ink_rect(file_name)


def resolve(file_name: str) -> dict:
    """How to draw the file an [img] points at (InlineIcons.resolve)."""
    if file_name in OPERATORS:
        return {"operator": OPERATORS[file_name]}
    for pattern, recipe in RECIPES:
        found = pattern.match(file_name)
        if found is None:
            continue
        fmt = lambda template: _format(template, found)
        return {
            "base": recipe.get("base"),
            "shape": recipe.get("shape", ""),
            "tint": recipe.get("base_tint", (255, 255, 255)),
            "symbol": True,
            "label": fmt(recipe["label"]),
            "label2": fmt(recipe.get("label2", "")),
            "label_color": recipe.get("label_color", LABEL_DARK),
        }
    return {"base": file_name, "shape": "", "tint": (255, 255, 255),
            "symbol": SYMBOL_FILES.match(file_name) is not None,
            "label": "", "label2": "", "label_color": LABEL_DARK}


def _format(template: str, found) -> str:
    out = template
    for group in range(1, (found.lastindex or 0) + 1):
        out = out.replace("{%d}" % group, found.group(group))
    return out


def _multiply(color_a, color_b):
    return tuple(round(a * b / 255) for a, b in zip(color_a, color_b))


def _hex(color: str | None):
    if not color:
        return None
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def _tinted(img: Image.Image, tint) -> Image.Image:
    """Godot's modulate: multiply RGB, keep alpha. A hexagon's black outline
    stays black under an element tint."""
    if tint is None or tuple(tint) == (255, 255, 255):
        return img
    import numpy as np
    a = np.asarray(img).astype(np.float32)
    a[..., :3] *= np.asarray(tint, dtype=np.float32) / 255.0
    return Image.fromarray(a.round().astype("uint8"), "RGBA")


# ---------------------------------------------------------------------------
# Building an icon
# ---------------------------------------------------------------------------

@lru_cache(maxsize=4096)
def build(file_name: str, height: float, color: str | None, size: int):
    """The icon a token expands to at font `size`. Cached: the typesetter's
    size search asks for the same icon at several sizes, and callers only ever
    read the image.

    `height` is the token's authored [img] height, `color` its tint ("#rrggbb"
    or None). Returns an `Icon`, or `("operator", text)` for and / or.
    """
    info = resolve(file_name)
    if "operator" in info:
        return ("operator", info["operator"])
    tint = _multiply(info["tint"], _hex(color) or (255, 255, 255))
    if info["shape"] == TAG:
        return _tag_icon(info["label"], tint, size)
    if info["shape"] == ESCALATE:
        h = ESCALATE_HEIGHT_FRACTION * SYMBOL_INK_EM * size
        if info["label"]:
            return pointer(info["label"], "", h)
        return pointer("", info["label2"], h)
    return _texture_icon(info, height, tint, size)


def _texture_icon(info: dict, height: float, tint, size: int) -> Icon:
    base = info["base"]
    tex = _texture(base)
    tw, th = tex.size
    ink = ink_rect(base)
    anchor = anchor_rect(base)
    if info["symbol"]:
        scale = SYMBOL_INK_EM * size / max(anchor[3] * th, 0.001)
    else:
        scale = height * size / BASE_FONT_SIZE / max(th, 0.001)
    w, h = max(1, round(tw * scale)), max(1, round(th * scale))
    ink_px = (ink[0] * w, ink[1] * h, ink[2] * w, ink[3] * h)
    anchor_px = (anchor[0] * w, anchor[1] * h, anchor[2] * w, anchor[3] * h)

    if not info["label"]:
        img = _tinted(tex.resize((w, h), Image.LANCZOS), tint)
        return Icon(img, ink_px, anchor_px)

    # A composite: the blank base, and its number set on the ink's centre.
    ss = SUPERSAMPLE
    big = _tinted(tex.resize((w * ss, h * ss), Image.LANCZOS), tint)
    ix, iy, iw, ih = ink_px
    label = info["label"]
    label_size = max(int(round(LABEL_CAP_FRACTION * ih / cap_ratio())), 1)
    width = text_width(label, label_size)
    max_width = LABEL_WIDTH_FRACTION * iw
    if width > max_width:
        label_size = max(int(label_size * max_width / width), 1)
    raise_by = LABEL_RAISE * ih
    cx, cy = ix + iw / 2, iy + ih / 2 - raise_by
    _draw_label(big, label, label_size, cx, cy + cap_height(label_size) / 2,
                info["label_color"], centred=True)
    return Icon(big.resize((w, h), Image.LANCZOS), ink_px, anchor_px)


def _tag_label_size(height: float) -> int:
    return max(int(round(TAG_LABEL_CAP_FRACTION * height / cap_ratio())), 1)


def _tag_icon(label: str, tint, size: int) -> Icon:
    """A bonus / mult number on a rounded tag that widens to fit it."""
    h = TAG_HEIGHT_FRACTION * SYMBOL_INK_EM * size
    label_size = _tag_label_size(h)
    w = max(h, text_width(label, label_size) + 2.0 * TAG_PADDING_FRACTION * h)
    W, H = max(1, round(w)), max(1, round(h))
    ss = SUPERSAMPLE
    big = Image.new("RGBA", (W * ss, H * ss), (0, 0, 0, 0))
    ImageDraw.Draw(big).rounded_rectangle(
        (0, 0, W * ss - 1, H * ss - 1), radius=TAG_RADIUS_FRACTION * H * ss,
        fill=tuple(tint) + (255,))
    _draw_label(big, label, label_size, W / 2, H / 2 + cap_height(label_size) / 2,
                LABEL_DARK, centred=True)
    box = (0.0, 0.0, float(W), float(H))
    return Icon(big.resize((W, H), Image.LANCZOS), box, box)


def _draw_label(big: Image.Image, text: str, label_size: int, x: float, baseline: float,
                color, centred: bool = False) -> None:
    """Text on a supersampled icon: `x` is its left edge, or its centre when
    `centred`, in the icon's 1x pixels. Stroked in its own colour first, which
    is how the game thickens Aldrich's one light weight toward the old baked
    digits (LABEL_STROKE_FRACTION)."""
    ss = SUPERSAMPLE
    f = font(label_size * ss)
    stroke = LABEL_STROKE_FRACTION * label_size
    # Godot's outline size and PIL's stroke_width are different units: outline 3
    # matches stroke 1 (card_layout.GODOT_OUTLINE_3), checked again here against
    # the game's own render of a tag.
    stroke_px = max(0, round(stroke / 3 * ss))
    if centred:
        x -= f.getlength(text) / ss / 2
    ImageDraw.Draw(big).text((x * ss, baseline * ss), text, font=f, anchor="ls",
                             fill=tuple(color) + (255,), stroke_width=stroke_px,
                             stroke_fill=tuple(color) + (255,))


# ---------------------------------------------------------------------------
# Weapon pointers
# ---------------------------------------------------------------------------

def _escalate_metrics(label: str, label2: str, h: float) -> dict:
    """Typesetter._escalate_metrics: x positions from the pointer's left edge."""
    is_damage = label != ""
    number = label if is_damage else label2
    cap = ESCALATE_DAMAGE_CAP if is_damage else ESCALATE_AMOUNT_CAP
    number_size = max(int(round(cap * h / cap_ratio())), 1)
    number_w = text_width(number, number_size)
    arrow_h = 0.0 if is_damage else ESCALATE_ARROW_HEIGHT * h
    arrow_w = arrow_h * 1.15
    arrow_gap = 0.0 if is_damage else 0.1 * h
    s = ESCALATE_SLANT * h
    pad = ESCALATE_PADDING * h
    b = s * 0.5
    c = b + pad + arrow_w + arrow_gap + number_w + 0.05 * h
    return {"h": h, "s": s, "pad": pad, "b": b, "c": c, "width": c + ESCALATE_POINT * h,
            "is_damage": is_damage, "number": number, "number_size": number_size,
            "arrow_w": arrow_w, "arrow_h": arrow_h, "arrow_gap": arrow_gap}


def escalate_outline(e: dict, o=(0.0, 0.0)) -> list:
    """The pointer's five corners before rounding (Typesetter.escalate_outline)."""
    h, s, b, c = e["h"], e["s"], e["b"], e["c"]
    ox, oy = o
    return [(ox + b + s * 0.5, oy), (ox + c, oy), (ox + e["width"], oy + h * 0.5),
            (ox + c, oy + h), (ox + b - s * 0.5, oy + h)]


def round_corners(poly: list, radius: float) -> list:
    """Each corner replaced by a quadratic curve leaving its edges `radius` from
    it (Typesetter._round_corners)."""
    if radius <= 0.0:
        return poly
    steps = 6
    out = []
    n = len(poly)
    for i in range(n):
        cx, cy = poly[i]
        px, py = poly[(i - 1) % n]
        nx, ny = poly[(i + 1) % n]
        d_prev = ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5
        d_next = ((nx - cx) ** 2 + (ny - cy) ** 2) ** 0.5
        r = min(radius, min(d_prev, d_next) * 0.5)
        p1 = (cx + (px - cx) / max(d_prev, 1e-9) * r, cy + (py - cy) / max(d_prev, 1e-9) * r)
        p2 = (cx + (nx - cx) / max(d_next, 1e-9) * r, cy + (ny - cy) / max(d_next, 1e-9) * r)
        for step in range(steps + 1):
            t = step / steps
            a, m, z = (1 - t) * (1 - t), 2 * (1 - t) * t, t * t
            out.append((p1[0] * a + cx * m + p2[0] * z, p1[1] * a + cy * m + p2[1] * z))
    return out


def pointer(label: str, label2: str, h: float, fill=None) -> Icon:
    """A weapon pointer `h` tall: a damage one for `label` (white, dark number)
    or an escalate one for `label2` (gold, a triangle then a dark brown number).

    `fill` overrides the body's colour -- the card's corner badge is the
    damage pointer in its element's colour (BaseCard._push_badge_uniforms)."""
    e = _escalate_metrics(label, label2, h)
    W, H = max(1, round(e["width"])), max(1, round(h))
    ss = SUPERSAMPLE
    big = Image.new("RGBA", (W * ss, H * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    corner = ESCALATE_CORNER * h
    body = fill or (ESCALATE_DAMAGE_COLOR if e["is_damage"] else ESCALATE_AMOUNT_COLOR)
    number_color = ESCALATE_DAMAGE_LABEL if e["is_damage"] else ESCALATE_AMOUNT_LABEL
    poly = round_corners(escalate_outline(e), corner)
    draw.polygon([(x * ss, y * ss) for x, y in poly], fill=tuple(body) + (255,))

    mid = h * 0.5
    x = e["b"] + e["pad"]
    if not e["is_damage"]:
        arrow_h, arrow_w = e["arrow_h"], e["arrow_w"]
        base_y = mid + arrow_h * 0.5
        arrow = round_corners([(x, base_y), (x + arrow_w * 0.5, base_y - arrow_h),
                               (x + arrow_w, base_y)], corner * 0.5)
        draw.polygon([(px * ss, py * ss) for px, py in arrow],
                     fill=tuple(number_color) + (255,))
        x += arrow_w + e["arrow_gap"]
    _draw_label(big, e["number"], e["number_size"], x,
                mid + cap_height(e["number_size"]) / 2, number_color)
    box = (0.0, 0.0, float(W), float(H))
    return Icon(big.resize((W, H), Image.LANCZOS), box, box)
