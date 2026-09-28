"""The /stats reports drawn as images: one house style for every chart.

Text in embeds could not do what the reports need. Discord wraps code blocks
rather than scrolling them, so every chart was squeezed into 24 monospace
characters, and ANSI text has eight colours (anima rendered blue). An image has
no width limit and any colour, and it looks the same on desktop and phone.

The design rules, from the dataviz guidance the reports were audited against
(2026-09-28):

  * Pick the form from the data's job. One number is a stat, not a one-bar
    chart; a rate against a baseline is a bar with a reference line.
  * No hover in an image, so values are labelled directly: every bar carries
    its number, and its sample size beside it.
  * Colour carries meaning only. One accent for a single measure, grey for
    what is not asserting anything (a low sample, a track). Text is never
    coloured to match a mark.
  * Quiet chrome: thin rounded bars on a track, hairline rules, room to breathe.
  * Every card says what it rests on, in its subtitle and its footnote.

Drawn at 2x (`SCALE`): Discord shows an embed image around 500px wide, and a
phone shrinks it further, so a 1000px image stays sharp in both. Sizes below
are in 1x points and multiplied at draw time.

A report is a `Card` of blocks stacked top to bottom. Each block knows its own
height, so a card is measured before it is drawn and never clipped. Blocks are
added here as reports need them, not ahead of time.

Pure drawing: no I/O, no Discord. The commands call `Card.png()` off the event
loop (asyncio.to_thread), since drawing blocks for a noticeable moment.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib
from PIL import Image, ImageDraw, ImageFont

SCALE = 2
WIDTH = 500
PAD = 24

# DejaVu Sans ships with matplotlib, a declared requirement, so the bot finds
# the same face on any machine it runs on without vendoring a font.
_FONT_DIR = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
_REGULAR = _FONT_DIR / "DejaVuSans.ttf"
_BOLD = _FONT_DIR / "DejaVuSans-Bold.ttf"

# Dark chart chrome. An image cannot follow the reader's Discord theme, and
# most Discord readers are on dark, so every card is a dark card.
SURFACE = "#1a1a19"
INK = "#ffffff"          # primary text
INK_2 = "#c3c2b7"        # secondary text
MUTED = "#898781"        # labels, counts, notes
TRACK = "#2c2c2a"        # a bar's empty track, and hairline rules
FADED = "#4a4a46"        # a bar that is shown but not asserted (low sample)
ACCENT = "#3987e5"       # the one colour for a single measure
NEUTRAL = "#6e6d68"      # an ordinary bar in an EMPHASIS chart, where colour
                         # is saved for the few rows that matter
# The diverging pair, for a value clearly on one side of its baseline. Red and
# blue read as opposites, and pass the colour-blindness and contrast checks on
# SURFACE (dataviz validate_palette.js, dark mode). Never used alone: a row in
# either colour also carries ▼ or ▲ in text.
BELOW = "#e66767"
ABOVE = "#3987e5"
REFERENCE = "#e8e6dc"    # a reference line across bars: near-white, so it
                         # reads over both the accent and the track

_fonts: dict = {}


def font(size: float, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = (size, bold)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(str(_BOLD if bold else _REGULAR),
                                         round(size * SCALE))
    return _fonts[key]


def px(points: float) -> int:
    return round(points * SCALE)


def signed(points: float) -> str:
    """`+12`, `−34`, `±0`. A real minus sign: a hyphen reads as a dash."""
    value = round(points)
    if value == 0:
        return "±0"
    return f"+{value}" if value > 0 else f"−{-value}"


class Block:
    """One horizontal band of a card. Height in 1x points."""
    height: float = 0

    def draw(self, d: ImageDraw.ImageDraw, top: int) -> None:
        raise NotImplementedError


@dataclass
class Spacer(Block):
    height: float = 8

    def draw(self, d, top):
        pass


@dataclass
class SectionHeader(Block):
    """A small uppercase label with a hairline running to the right edge, and
    optional detail after it in a quieter ink."""
    label: str
    detail: str = ""
    height: float = 28

    def draw(self, d, top):
        mid = top + px(self.height) // 2 + px(2)
        label = self.label.upper()
        d.text((px(PAD), mid), label, font=font(12, True), fill=MUTED, anchor="lm")
        x = px(PAD) + d.textlength(label, font=font(12, True)) + px(8)
        if self.detail:
            d.text((x, mid), self.detail, font=font(12), fill=MUTED, anchor="lm")
            x += d.textlength(self.detail, font=font(12)) + px(8)
        d.line([(x, mid), (px(WIDTH - PAD), mid)], fill=TRACK, width=px(0.5))


@dataclass
class BarRow(Block):
    """A labelled bar on a 0-1 track, its value directly labelled.

    `value` None draws the empty track and `empty_text` in place of numbers.
    `faded` greys the bar and dims the text: shown, but not asserted.
    `reference` draws a vertical line across the bar at that 0-1 position,
    for a baseline the value should be read against (an act's average), and
    `delta` is the text stating the difference, shown in its own column.
    `fill` overrides the bar colour (an emphasis chart's neutral, or a flag).
    `marker` is a symbol (▼ ▲) drawn left of the delta in `marker_fill`: the
    coloured mark beside the text that carries a flag, so it is never colour
    alone and never lost when the bar has no length.

    A value of exactly 0 still draws: a dot at the start of the track. A 0%
    boss is the one most worth seeing, and a bar with no length is invisible.
    """
    label: str
    value: float | None
    value_text: str = ""
    count_text: str = ""
    delta: str = ""
    faded: bool = False
    reference: float | None = None
    fill: str | None = None
    marker: str = ""
    marker_fill: str | None = None
    empty_text: str = "no data"
    height: float = 28

    LABEL_W = 84
    VALUE_W = 44
    DELTA_W = 56
    COUNT_W = 54

    def draw(self, d, top):
        cy = top + px(self.height) // 2
        bar_x0 = px(PAD + self.LABEL_W)
        bar_x1 = px(WIDTH - PAD - self.VALUE_W - self.DELTA_W - self.COUNT_W - 8)
        bar_h = px(12)
        y0, y1 = cy - bar_h // 2, cy + bar_h // 2

        d.text((px(PAD), cy), self.label, font=font(15),
               fill=INK_2 if self.faded or self.value is None else INK, anchor="lm")
        d.rounded_rectangle([bar_x0, y0, bar_x1, y1], radius=bar_h // 2, fill=TRACK)

        if self.value is None:
            d.text((px(WIDTH - PAD), cy), self.empty_text, font=font(13),
                   fill=MUTED, anchor="rm")
        else:
            x = bar_x0 + (bar_x1 - bar_x0) * min(max(self.value, 0), 1)
            d.rounded_rectangle([bar_x0, y0, max(x, bar_x0 + bar_h), y1],
                                radius=bar_h // 2,
                                fill=FADED if self.faded else (self.fill or ACCENT))
            right = px(WIDTH - PAD)
            d.text((right, cy), self.count_text, font=font(13), fill=MUTED, anchor="rm")
            right -= px(self.COUNT_W)
            if self.delta:
                d.text((right, cy), self.delta, font=font(13),
                       fill=MUTED if self.faded else INK_2, anchor="rm")
            if self.marker:
                gap = d.textlength(self.delta, font=font(13)) + px(5) if self.delta else 0
                d.text((right - gap, cy), self.marker, font=font(13),
                       fill=self.marker_fill or INK_2, anchor="rm")
            right -= px(self.DELTA_W)
            d.text((right, cy), self.value_text, font=font(15, True),
                   fill=INK_2 if self.faded else INK, anchor="rm")

        if self.reference is not None:
            x = bar_x0 + (bar_x1 - bar_x0) * min(max(self.reference, 0), 1)
            d.line([(x, y0 - px(4)), (x, y1 + px(4))], fill=REFERENCE, width=px(1.5))


@dataclass
class Note(Block):
    """A line of small muted text: a definition, a caveat, a legend."""
    text: str
    height: float = 20

    def draw(self, d, top):
        d.text((px(PAD), top + px(self.height) // 2), self.text, font=font(12),
               fill=MUTED, anchor="lm")


@dataclass
class Card:
    """A report image: a title, a subtitle saying what it rests on, blocks."""
    title: str
    subtitle: str = ""
    blocks: list = field(default_factory=list)

    HEAD = 64

    def add(self, *blocks: Block) -> "Card":
        self.blocks.extend(blocks)
        return self

    def render(self) -> Image.Image:
        height = PAD + self.HEAD + sum(b.height for b in self.blocks) + PAD
        img = Image.new("RGBA", (px(WIDTH), px(height)), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=px(12), fill=SURFACE)
        d.text((px(PAD), px(PAD)), self.title, font=font(22, True), fill=INK)
        if self.subtitle:
            d.text((px(PAD), px(PAD + 30)), self.subtitle, font=font(13), fill=INK_2)
        top = px(PAD + self.HEAD)
        for block in self.blocks:
            block.draw(d, top)
            top += px(block.height)
        return img

    def png(self) -> bytes:
        buffer = io.BytesIO()
        self.render().save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
