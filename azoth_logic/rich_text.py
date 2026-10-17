"""Card rules text: symbol tokens, wrapping, and placement.

Card text is authored with inline tokens -- `"Draw 1, Gain [1life] per card
drawn this link"`. The game expands each into BBCode, and since 2026-09-27 its
typesetter draws that (azoth `docs/TYPESETTER.md`). Here every token becomes an
`inline_icons.Icon`, built the way the typesetter builds it: numbers set on
blank bases, bonus and mult as tags, weapon pointers, all sized off the font
size and placed by their ink.

Two token sources, as in `Utils.replace_icon_from_dict`:

  * the table generated from `Utils.replace_dict` by `tools/sync_assets.py`, so
    a new table token needs a sync, not a code change;
  * the weapon tokens, `[20damage]` and `[escalate3]`, which have no table
    entry: the number is read out of the token (`inline_icons.WEAPON_TOKEN`).

Text also carries `{...}` display placeholders, which `tokenize` resolves through
`placeholders.resolve` before it looks for a symbol -- see that function and
`azoth_logic/placeholders.py`.

Rules text is laid out by `typeset`, which runs the game's typesetter search
(`azoth_logic/typesetter.py`: size, breaks, lobe margins, centring on the ink)
over the words built here. Names and subtypes use `layout` / `draw_centered`,
plain greedy rows in a Label's box, as the game's Labels are.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import ImageDraw

from azoth_logic import inline_icons as I
from azoth_logic import placeholders

ASSET_ROOT = Path(__file__).resolve().parent.parent / "assets"
SYMBOL_DIR = ASSET_ROOT / "card_art" / "symbols"
SYMBOL_MAP = ASSET_ROOT / "card_art" / "card_symbols.json"

# The rules text's preferred size (TypesetLabel.preferred_font_size).
PREFERRED_SIZE = I.BASE_FONT_SIZE
# Typesetter.DESCENDERS: a line holding one of these reaches below the baseline.
DESCENDERS = "gjpqy,;()|"

# BBCode the game wraps text in that carries no visual meaning here.
_STRIP_TAGS = re.compile(r"\[/?(?:center|b|i|u|url[^\]]*)\]")

_token_cache = None
_pattern_cache = None


def _tokens() -> dict:
    global _token_cache
    if _token_cache is None:
        if not SYMBOL_MAP.is_file():
            raise FileNotFoundError(
                f"{SYMBOL_MAP} is missing. Run: python -m tools.sync_assets --azoth <path>")
        _token_cache = json.loads(SYMBOL_MAP.read_text())["tokens"]
    return _token_cache


def _pattern():
    """Table tokens longest first, so `[10life]` is not eaten by `[1life]`, then
    the weapon tokens."""
    global _pattern_cache
    if _pattern_cache is None:
        table = "|".join(re.escape(t) for t in sorted(_tokens(), key=len, reverse=True))
        _pattern_cache = re.compile(
            (table + "|" if table else "") + I.WEAPON_TOKEN.pattern)
    return _pattern_cache


def _icon_for(token: str, size: int):
    """The Icon (or ("operator", text)) one token stands for at `size`."""
    info = _tokens().get(token)
    if info is not None:
        return I.build(info["file"], I.icon_height_for(token), info.get("color"), size)
    weapon = I.WEAPON_TOKEN.fullmatch(token)
    if weapon is not None:
        return I.build(weapon.group(1) + ".png", I.REPLACE_FONT_SIZE, None, size)
    return None


def tokenize(text: str, size: int = PREFERRED_SIZE):
    """Split card text into runs: ("text", str), ("bold", str) or ("img", Icon).

    `size` is the font size the text is set at; every icon is sized from it.
    `bold` is an and / or connector, set as a word of its own.

    `{...}` DISPLAY PLACEHOLDERS RESOLVE HERE, and here only. Every string any
    renderer draws reaches PIL through this function, which makes it the one
    place the substitution cannot be forgotten on a new surface. `/show` prints
    text without drawing it, so it calls `placeholders.resolve` itself.

    Placeholders resolve BEFORE symbols, which is the game's order too: a token
    can sit inside a symbol (`"Heal [{levelup.level}life]"`).
    """
    text = placeholders.resolve(_STRIP_TAGS.sub("", text))
    runs, pos = [], 0
    for m in _pattern().finditer(text):
        icon = _icon_for(m.group(0), size)
        if icon is None:
            continue
        if m.start() > pos:
            runs.append(("text", text[pos:m.start()]))
        if isinstance(icon, tuple):
            runs.append(("bold", icon[1]))
        else:
            runs.append(("img", icon))
        pos = m.end()
    if pos < len(text):
        runs.append(("text", text[pos:]))
    return runs


def is_lone_icon(text: str) -> bool:
    """Whether the whole text is one icon and nothing else (Amplis's
    `[2bonus]`, Fervor's `[magnify_2]`), which the game sets larger."""
    runs = [r for r in tokenize(text) if not (r[0] == "text" and not r[1].strip())]
    return len(runs) == 1 and runs[0][0] == "img"


# ---------------------------------------------------------------------------
# Words and lines
# ---------------------------------------------------------------------------

def _atoms(runs, font):
    """Runs as atoms: ("word", str, w), ("bold", str, w), ("img", Icon, w),
    ("space", str, w) and ("break", "", 0) for an authored newline. An icon
    occupies its INK width."""
    out = []
    for kind, value in runs:
        if kind == "img":
            out.append(("img", value, value.ink_width))
            continue
        if kind == "bold":
            # A word of its own, with ordinary spacing either side, even where
            # the source runs icons together ("[2valence][and][3valence]").
            space = ("space", " ", font.getlength(" "))
            if out and out[-1][0] not in ("space", "break"):
                out.append(space)
            out.append(("bold", value, font.getlength(value)))
            out.append(space)
            continue
        for piece in re.split(r"(\n|[ \t]+)", value):
            if piece == "":
                continue
            if piece == "\n":
                out.append(("break", "", 0.0))
            elif piece.isspace():
                if out and out[-1][0] == "space":
                    continue
                out.append(("space", piece, font.getlength(piece)))
            else:
                out.append(("word", piece, font.getlength(piece)))
    return out


def _words(atoms):
    """Group atoms into breakable words. A word runs between spaces and can mix
    text and icons ("[life]," is one word). Returns a list of paragraphs, each a
    list of (word_atoms, space_before_width)."""
    paragraphs, para, word, space = [], [], [], 0.0
    for atom in atoms:
        kind = atom[0]
        if kind == "break":
            if word:
                para.append((word, space))
            paragraphs.append(para)
            para, word, space = [], [], 0.0
        elif kind == "space":
            if word:
                para.append((word, space))
                word = []
            space = atom[2] if para else 0.0
        else:
            if not word and not para:
                space = 0.0
            word.append(atom)
    if word:
        para.append((word, space))
    paragraphs.append(para)
    return paragraphs


def layout(text, font, max_width):
    """Wrap into lines. Returns [[atom, ...], ...], no leading or trailing
    spaces. Breaks only between words; authored newlines always break.

    Icons are sized from `font`'s size.
    """
    atoms = _atoms(tokenize(text, int(font.size)), font)
    lines = []
    for para in _words(atoms):
        lines.extend(layout_paragraph(para, max_width))
    return lines


def _join(words):
    line = []
    for word, space in words:
        if line:
            line.append(("space", " ", space))
        line.extend(word)
    return line


def layout_paragraph(para, max_width):
    """One paragraph's words wrapped greedily: the fewest lines that fit."""
    lines, line, width = [], [], 0.0
    for word, space in para:
        word_w = sum(a[2] for a in word)
        if line and width + space + word_w > max_width:
            lines.append(_join(line))
            line, width = [], 0.0
        width += (space if line else 0.0) + word_w
        line.append((word, space))
    if line:
        lines.append(_join(line))
    return lines


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

def _line_reach(line, size: int, ink: bool):
    """(above, below) the baseline for one line.

    `ink`: measured to the glyphs -- cap height, and the descender only if the
    line has one -- as the typesetter centres a block. Otherwise to the font's
    ascent and descent, the box a Label lays a line in.
    """
    font = I.font(size)
    if ink:
        has_text = any(a[0] in ("word", "bold") for a in line)
        above = I.cap_height(size) if has_text else 0.0
        below = I.descender(size) if any(
            a[0] in ("word", "bold") and any(c in DESCENDERS for c in a[1]) for a in line) else 0.0
    else:
        ascent, descent = font.getmetrics()
        above, below = float(ascent), float(descent)
    icon_mid = I.ICON_CENTRE * I.cap_height(size)
    for a in line:
        if a[0] == "img":
            up, down = a[1].reach(icon_mid)
            above, below = max(above, up), max(below, down)
    return above, below


def _draw_line(canvas, line, x, baseline, size, color, stroke=0, stroke_color=(0, 0, 0)):
    draw = ImageDraw.Draw(canvas)
    font = I.font(size)
    icon_mid = I.ICON_CENTRE * I.cap_height(size)
    bold_stroke = max(1, round(I.LABEL_STROKE_FRACTION * size / 3))
    for kind, value, aw in line:
        if kind == "img":
            ax, ay, aw_, ah = value.anchor
            ix = x - value.ink[0]
            iy = baseline - icon_mid - (ay + ah / 2)
            canvas.alpha_composite(value.image, (round(ix), round(iy)))
        elif kind == "bold":
            draw.text((x, baseline), value, font=font, fill=color, anchor="ls",
                      stroke_width=max(stroke, bold_stroke), stroke_fill=color)
        elif kind == "word":
            draw.text((x, baseline), value, font=font, fill=color, anchor="ls",
                      stroke_width=stroke, stroke_fill=stroke_color)
        x += aw


def measure(lines, font, line_spacing):
    """The height of `lines` laid out as Label rows (names, subtypes)."""
    size = int(font.size)
    height = 0.0
    for ln in lines:
        above, below = _line_reach(ln, size, ink=False)
        height += above + below + line_spacing
    return height - line_spacing if lines else 0.0


def draw_centered(canvas, lines, font, box, color, line_spacing=0,
                  stroke=0, stroke_color=(0, 0, 0)):
    """Draw wrapped lines centred horizontally in `box`, starting at its top.

    Each row is as tall as the font's ascent + descent, or taller where an icon
    reaches past them. Icons are centred on the capitals by their anchor."""
    x0, y0, w, _ = box
    size = int(font.size)
    y = y0
    for ln in lines:
        above, below = _line_reach(ln, size, ink=False)
        total = sum(a[2] for a in ln)
        _draw_line(canvas, ln, x0 + (w - total) / 2, y + above, size, color,
                   stroke, stroke_color)
        y += above + below + line_spacing
    return y - y0


# ---------------------------------------------------------------------------
# The typeset rules text
# ---------------------------------------------------------------------------

@dataclass
class Typeset:
    """Rules text placed in its region. `lines` are (atoms, x, baseline)."""
    size: int
    lines: list = field(default_factory=list)
    ink_top: float = 0.0
    ink_bottom: float = 0.0
    fits: bool = True


def typeset(text: str, region, lone_icon: bool = True) -> Typeset:
    """Lay rules text out the way TypesetLabel does, in a `typesetter.Region`.

    The size and breaks come from the typesetter's search (`typesetter`); a
    text that is one icon alone is set at `LONE_ICON_SCALE` x the preferred
    size instead (card faces only; `lone_icon=False` turns it off). Each line
    is centred on the region's centre line by its drawn width.
    """
    from azoth_logic import typesetter as T

    def paragraphs_at(size):
        return _words(_atoms(tokenize(text, size), I.font(size)))

    best = None
    if lone_icon and is_lone_icon(text):
        size = int(round(T.PREFERRED_SIZE * I.LONE_ICON_SCALE))
        best = T.best_at_size(paragraphs_at(size), region, size)
    if best is None:
        sizes = sorted(range(T.MIN_SIZE, T.MAX_SIZE + 1), key=T.size_cost)
        for size in sizes:
            if best is not None and T.size_cost(size) >= best["cost"]:
                break
            candidate = T.best_at_size(paragraphs_at(size), region, size)
            if candidate is not None and (best is None or candidate["cost"] < best["cost"]):
                best = candidate
    if best is None:
        best = T.best_at_size(paragraphs_at(T.MIN_SIZE), region, T.MIN_SIZE, allow_overflow=True)
        if best is None:
            return Typeset(size=T.PREFERRED_SIZE)
        best["fits"] = False

    result = Typeset(size=best["size"], ink_top=best["ink_top"],
                     ink_bottom=best["ink_bottom"], fits=best["fits"])
    words = best["words"]
    centre = region.centre_x
    for line in best["lines"]:
        chosen = [(w["atoms"], w["space"]) for w in words[line["first"]:line["end"]]]
        atoms = _join(chosen)
        width = sum(a[2] for a in atoms)
        result.lines.append((atoms, centre - width / 2, line["baseline"]))
    return result


def draw_typeset(canvas, laid: Typeset, color) -> None:
    for ln, x, baseline in laid.lines:
        _draw_line(canvas, ln, x, baseline, laid.size, color)
