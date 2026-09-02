"""Card rules text: symbol tokens, wrapping, and centred layout.

Card text is authored with inline tokens -- `"Draw 1, Gain [1life] per card
drawn this link"`. The game expands each into BBCode that Godot's RichTextLabel
draws as an inline image. Here they become PIL images composited onto the text
baseline.

The token table is generated from the game by `tools/sync_assets.py`; this
module only consumes it, so a new token in the game needs a sync, not a code
change.

One rule carried over from `Utils.replace_icon_from_dict`: life, mult and bonus
symbols render at **1.35x** the surrounding font size. They are numerals inside a
glyph and read too small at parity.

Text also carries `{...}` display placeholders, which `tokenize` resolves through
`placeholders.resolve` before it looks for a symbol -- see that function and
`azoth_logic/placeholders.py`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from azoth_logic import placeholders

ASSET_ROOT = Path(__file__).resolve().parent.parent / "assets"
SYMBOL_DIR = ASSET_ROOT / "card_art" / "symbols"
SYMBOL_MAP = ASSET_ROOT / "card_art" / "card_symbols.json"

# Utils.replace_icon_from_dict: these token families are drawn oversized.
_OVERSIZED = ("life", "mult", "bonus")
_OVERSIZE_FACTOR = 1.35

# Symbols do NOT scale with the label's font size.
#
# base_card.gd:979 calls `Utils.replace_icon_from_dict(resolved_base)` with no
# size argument, so every card symbol is sized from that function's `font_size =
# 50` default -- even though TextLabel's own font is 40. Sizing symbols off the
# label size (the obvious reading) makes them 20% too small.
#
# Confirmed by measurement: rendering one token on an otherwise identical card
# in Godot and differencing against a blank one gives a base symbol height of
# 1.25 x the label's 40px font, i.e. exactly 50px, across [3valence], [ether]
# and [up]. The life/mult/bonus family comes out proportionally larger.
SYMBOL_BASE_SIZE = 50

# BBCode the game wraps text in that carries no visual meaning here.
_STRIP_TAGS = re.compile(r"\[/?(?:center|b|i|u|url[^\]]*)\]")

_token_cache = None
_symbol_cache: dict = {}


def _tokens() -> dict:
    global _token_cache
    if _token_cache is None:
        if not SYMBOL_MAP.is_file():
            raise FileNotFoundError(
                f"{SYMBOL_MAP} is missing. Run: python -m tools.sync_assets --azoth <path>")
        _token_cache = json.loads(SYMBOL_MAP.read_text())["tokens"]
    return _token_cache


def _tint(img: Image.Image, hex_color: str) -> Image.Image:
    """Recolour a symbol while keeping its alpha.

    Godot's [color] tag does not tint inline images, so the game injects
    `color=#hex` into the img tag itself for element symbols. Same idea here.
    """
    rgb = tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    solid = Image.new("RGBA", img.size, rgb + (255,))
    solid.putalpha(img.getchannel("A"))
    return solid


def _symbol(file_name: str, height: int, color: str | None) -> Image.Image:
    key = (file_name, height, color)
    if key in _symbol_cache:
        return _symbol_cache[key]
    path = SYMBOL_DIR / file_name
    if not path.is_file():
        raise FileNotFoundError(f"symbol {file_name} not vendored; run tools/sync_assets.py")
    img = Image.open(path).convert("RGBA")
    if color:
        img = _tint(img, color)
    scale = height / img.height
    img = img.resize((max(1, round(img.width * scale)), height), Image.LANCZOS)
    _symbol_cache[key] = img
    return img


def tokenize(text: str):
    """Split card text into runs: ("text", str) and ("img", Image).

    Longest token first, so `[10life]` is not eaten by `[1life]` -- the game's
    dict iteration order gives it the same protection by accident; being explicit
    here means it does not depend on dict ordering.

    Takes NO font size. Symbols are sized from SYMBOL_BASE_SIZE regardless of the
    surrounding label -- see that constant. This used to accept a `font_size` and
    ignore it, which invited exactly the bug the constant documents.

    `{...}` DISPLAY PLACEHOLDERS RESOLVE HERE, and here only. Every string any
    renderer draws -- card text, aspect text, rite text, names, subtypes, and so
    every deck, comparison and search sheet built out of them -- reaches PIL
    through this function, which makes it the one place the substitution cannot
    be forgotten on a new surface. The game funnels its own two surfaces through
    `CardTextComposer` for the same reason. `/show` prints text without drawing
    it, so it calls `placeholders.resolve` itself.

    Placeholders resolve BEFORE symbols, which is the game's order too: a token
    can sit inside a symbol (`"Heal [{levelup.level}life]"` is `[1life]` only
    once the inner one is gone). No live row does that here -- levelups are not
    rendered -- so the ordering is free insurance rather than a fix.
    """
    text = placeholders.resolve(_STRIP_TAGS.sub("", text))
    tokens = _tokens()
    pattern = re.compile("|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True)))

    runs, pos = [], 0
    for m in pattern.finditer(text):
        if m.start() > pos:
            runs.append(("text", text[pos:m.start()]))
        info = tokens[m.group(0)]
        name = m.group(0)
        base = SYMBOL_BASE_SIZE
        height = round(base * _OVERSIZE_FACTOR) if any(k in name for k in _OVERSIZED) else base
        runs.append(("img", _symbol(info["file"], height, info.get("color"))))
        pos = m.end()
    if pos < len(text):
        runs.append(("text", text[pos:]))
    return runs


def _atoms(runs, font):
    """Break runs into wrappable atoms: words, spaces and images."""
    out = []
    for kind, value in runs:
        if kind == "img":
            out.append(("img", value, value.width))
            continue
        for piece in re.split(r"(\s+)", value):
            if piece == "":
                continue
            w = font.getlength(piece)
            out.append(("space" if piece.isspace() else "word", piece, w))
    return out


def layout(text, font, max_width):
    """Wrap into lines. Returns [[atom, ...], ...] with trailing spaces dropped.

    No font size: `font` carries it for the text, and symbols are sized
    independently (SYMBOL_BASE_SIZE). The parameter used to exist only to forward
    to `tokenize`, which ignored it.
    """
    lines, line, width = [], [], 0.0
    for atom in _atoms(tokenize(text), font):
        kind, _, w = atom
        if kind == "space" and not line:
            continue                       # no leading space after a wrap
        if line and width + w > max_width and kind != "space":
            while line and line[-1][0] == "space":
                width -= line.pop()[2]
            lines.append(line)
            line, width = [], 0.0
        line.append(atom)
        width += w
    while line and line[-1][0] == "space":
        line.pop()
    if line:
        lines.append(line)
    return lines


def measure(lines, font, line_spacing):
    ascent, descent = font.getmetrics()
    line_h = ascent + descent
    height = 0.0
    for ln in lines:
        tallest = max([a[1].height for a in ln if a[0] == "img"] + [line_h])
        height += tallest + line_spacing
    return height - line_spacing if lines else 0.0


def draw_centered(canvas, lines, font, box, color, line_spacing=0,
                  stroke=0, stroke_color=(0, 0, 0)):
    """Draw wrapped lines centred horizontally in `box` and starting at its top.

    Images sit on the text's visual centre rather than its baseline, matching
    the `[img=c,c,...]` tag's centre alignment.
    """
    x0, y0, w, _ = box
    draw = ImageDraw.Draw(canvas)
    ascent, descent = font.getmetrics()
    line_h = ascent + descent
    y = y0
    for ln in lines:
        tallest = max([a[1].height for a in ln if a[0] == "img"] + [line_h])
        total = sum(a[2] for a in ln)
        x = x0 + (w - total) / 2
        for kind, value, aw in ln:
            if kind == "img":
                canvas.alpha_composite(value, (round(x), round(y + (tallest - value.height) / 2)))
            else:
                draw.text((x, y + (tallest - line_h) / 2), value, font=font, fill=color,
                          stroke_width=stroke, stroke_fill=stroke_color)
            x += aw
        y += tallest + line_spacing
    return y - y0
