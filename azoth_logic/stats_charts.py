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
# The game's act colours (GlobalVars.ACT_COLORS), acts 1-5, so a chart about
# acts matches what players see in the game. Checked as adjacent categories on
# SURFACE: every neighbouring pair clears the colour-blindness floor with room
# to spare. Act 3's violet sits just under 3:1 contrast, which the surface gaps
# between segments and the run count inside each segment cover.
ACT_COLOURS = ["#599830", "#06a6f6", "#841def", "#ff0000", "#f2b514"]

# The label column every row type shares, so rows of different blocks line up.
LABEL_W = 84

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
    # Column widths, in points. Defaults fit boss names and "14/19"; a report
    # with longer labels ("Embellished") or counts ("300/900") widens them and
    # the bar gives up the difference.
    label_w: float = LABEL_W
    count_w: float = 54
    tag: str = ""              # small muted text after the label ("aspect")
    height: float = 28

    LABEL_W = LABEL_W
    VALUE_W = 44
    DELTA_W = 56
    COUNT_W = 54

    def draw(self, d, top):
        cy = top + px(self.height) // 2
        bar_x0 = px(PAD + self.label_w)
        bar_x1 = px(WIDTH - PAD - self.VALUE_W - self.DELTA_W - self.count_w - 8)
        bar_h = px(12)
        y0, y1 = cy - bar_h // 2, cy + bar_h // 2

        d.text((px(PAD), cy), self.label, font=font(15),
               fill=INK_2 if self.faded or self.value is None else INK, anchor="lm")
        if self.tag:
            x = px(PAD) + d.textlength(self.label, font=font(15)) + px(5)
            d.text((x, cy + px(1)), self.tag, font=font(11), fill=MUTED, anchor="lm")
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
            right -= px(self.count_w)
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


def dim(colour: str, keep: float = 0.4) -> str:
    """A colour pulled toward the surface: shown, but not asserted. For marks
    whose hue carries meaning (an act colour) where FADED would erase it."""
    c = [int(colour[i:i + 2], 16) for i in (1, 3, 5)]
    base = [int(SURFACE[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(b + (a - b) * keep):02x}" for a, b in zip(c, base))


def _ink_on(colour: str) -> str:
    """Black or white, whichever reads on `colour`: the one exception to
    text never wearing a data colour is text set INSIDE a coloured fill."""
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return "#000000" if 0.299 * r + 0.587 * g + 0.114 * b > 150 else "#ffffff"


@dataclass
class ColumnHeads(Block):
    """Small muted headings over the columns below: `(text, x_points, anchor)`,
    anchor "lm" for a heading over a left edge, "rm" over a right edge."""
    heads: list
    height: float = 18

    def draw(self, d, top):
        cy = top + px(self.height) // 2
        for text, x, anchor in self.heads:
            d.text((px(x), cy), text, font=font(11), fill=MUTED, anchor=anchor)


@dataclass
class Legend(Block):
    """Colour swatches, named: `[(label, colour)]`.

    HOUSE RULE (Turner, 2026-09-28): a legend lists only what the chart above
    it actually draws. An act nobody reached or a surface nobody used is not a
    key to anything, only something to read past. Callers pass the present
    items; `act_legend` does it for acts.
    """
    items: list
    height: float = 22

    def draw(self, d, top):
        cy = top + px(self.height) // 2
        x = px(PAD + LABEL_W)
        for label, colour in self.items:
            d.rounded_rectangle([x, cy - px(5), x + px(10), cy + px(5)], radius=px(2), fill=colour)
            x += px(14)
            d.text((x, cy), label, font=font(11), fill=MUTED, anchor="lm")
            x += d.textlength(label, font=font(11)) + px(12)


def act_legend(acts) -> Legend:
    """The legend for the acts in `acts` (any iterable of act numbers), in
    order, and no others."""
    present = sorted({min(max(int(a), 1), len(ACT_COLOURS)) for a in acts})
    return Legend([(f"Act {a}", ACT_COLOURS[a - 1]) for a in present])


@dataclass
class ActStripRow(Block):
    """A group's runs as one bar split by the furthest act each reached, in the
    act colours with each segment's run count inside it, then the share that
    beat act 3 and the run count.

    Segments touch with a thin surface gap, never a border. `faded` dims the
    colours rather than greying them, so a low-sample row still says which
    acts. `marker` / `marker_fill` flag the rate, as on BarRow.
    """
    label: str
    acts: dict           # {furthest act: runs}
    cleared: int
    faded: bool = False
    marker: str = ""
    marker_fill: str | None = None
    strong: bool = False
    height: float = 30

    VALUE_W = 50
    COUNT_W = 40

    def draw(self, d, top):
        cy = top + px(self.height) // 2
        x0 = px(PAD + LABEL_W)
        x1 = px(WIDTH - PAD - self.VALUE_W - self.COUNT_W - 8)
        h = px(18)
        runs = sum(self.acts.values())
        d.text((px(PAD), cy), self.label, font=font(15, self.strong),
               fill=INK_2 if self.faded else INK, anchor="lm")

        filled = [(act, n) for act, n in sorted(self.acts.items()) if n]
        gap = px(1.5)
        x = x0
        for i, (act, n) in enumerate(filled):
            width = (x1 - x0) * n / runs
            right = x + width - (gap if i < len(filled) - 1 else 0)
            colour = ACT_COLOURS[min(max(act, 1), len(ACT_COLOURS)) - 1]
            if self.faded:
                colour = dim(colour)
            d.rounded_rectangle([x, cy - h // 2, max(right, x + px(2)), cy + h // 2],
                                radius=px(3), fill=colour)
            count = str(n)
            if right - x >= d.textlength(count, font=font(10, True)) + px(8):
                d.text(((x + right) / 2, cy), count, font=font(10, True),
                       fill=_ink_on(colour), anchor="mm")
            x += width

        right = px(WIDTH - PAD)
        d.text((right, cy), str(runs), font=font(13), fill=MUTED, anchor="rm")
        right -= px(self.COUNT_W)
        rate = f"{round(100 * self.cleared / runs)}%" if runs else "—"
        d.text((right, cy), rate, font=font(15, True),
               fill=INK_2 if self.faded else INK, anchor="rm")
        if self.marker:
            w = d.textlength(rate, font=font(15, True))
            d.text((right - w - px(4), cy), self.marker, font=font(13),
                   fill=self.marker_fill or INK_2, anchor="rm")


@dataclass
class MetricRow(Block):
    """A group's values in equal columns, each a small bar and its number.

    `values` is `[(value, scale_max)]`: each column scales to its own maximum,
    so a caller passes a real ceiling where one exists (links: the node
    budget) and the largest group's value where none does.
    """
    label: str
    values: list
    faded: bool = False
    height: float = 26

    def draw(self, d, top):
        cy = top + px(self.height) // 2
        d.text((px(PAD), cy), self.label, font=font(15),
               fill=INK_2 if self.faded else INK, anchor="lm")
        col_w = (WIDTH - PAD * 2 - LABEL_W) / len(self.values)
        h = px(8)
        for i, (value, scale) in enumerate(self.values):
            cx0 = PAD + LABEL_W + i * col_w
            bar_x0, bar_x1 = px(cx0), px(cx0 + col_w - 44)
            d.rounded_rectangle([bar_x0, cy - h // 2, bar_x1, cy + h // 2], radius=h // 2, fill=TRACK)
            if value is None:
                d.text((px(cx0 + col_w - 6), cy), "—", font=font(14), fill=MUTED, anchor="rm")
                continue
            if value > 0 and scale:
                x = bar_x0 + (bar_x1 - bar_x0) * min(value / scale, 1)
                d.rounded_rectangle([bar_x0, cy - h // 2, max(x, bar_x0 + h), cy + h // 2],
                                    radius=h // 2, fill=FADED if self.faded else NEUTRAL)
            d.text((px(cx0 + col_w - 6), cy), f"{value:.1f}", font=font(14, True),
                   fill=INK_2 if self.faded else INK, anchor="rm")


def column_x(index: int, columns: int) -> float:
    """The left edge, in points, of MetricRow column `index` of `columns`, for
    a ColumnHeads heading over it."""
    return PAD + LABEL_W + index * (WIDTH - PAD * 2 - LABEL_W) / columns


@dataclass
class Rule(Block):
    """A hairline across the card, above a totals row."""
    height: float = 8

    def draw(self, d, top):
        y = top + px(self.height) // 2
        d.line([(px(PAD), y), (px(WIDTH - PAD), y)], fill=TRACK, width=px(0.5))


@dataclass
class StatTiles(Block):
    """A row of headline numbers, label over value: when the answer is a
    number, the number is the chart. `[(label, value)]`, spread evenly over
    `columns` slots so rows of tiles line up."""
    tiles: list
    columns: int = 4
    height: float = 58

    def draw(self, d, top):
        w = (WIDTH - PAD * 2) / self.columns
        for i, (label, value) in enumerate(self.tiles):
            x = px(PAD + i * w)
            d.text((x, top + px(8)), label, font=font(11), fill=MUTED)
            d.text((x, top + px(24)), value, font=font(22, True), fill=INK)


def tile_rows(tiles: list, columns: int = 4, height: float = 52) -> list:
    """`tiles` wrapped into StatTiles rows of `columns`."""
    return [StatTiles(tiles[i:i + columns], columns, height)
            for i in range(0, len(tiles), columns)]


@dataclass
class TimeRow(Block):
    """A total split into coloured parts on one bar whose LENGTH is the total
    against `scale`: how much, and how it divides. Then `total_text` and up to
    two small trailing columns. `parts` is `[(amount, colour)]`; an empty or
    all-zero `parts` draws the bare track and a dash."""
    label: str
    parts: list
    scale: float
    total_text: str = "—"
    extra: tuple = ()          # up to two right-hand values, rightmost last
    bar: bool = True           # False: no track and no total, just the columns
    height: float = 28

    TOTAL_W, EXTRA_W = 48, 40

    def draw(self, d, top):
        cy = top + px(self.height) // 2
        d.text((px(PAD), cy), self.label, font=font(15), fill=INK, anchor="lm")
        right = px(WIDTH - PAD)
        for j, value in enumerate(reversed(self.extra)):
            d.text((right, cy), value, font=font(13), fill=MUTED if j == 0 else INK_2, anchor="rm")
            right -= px(self.EXTRA_W)
        if not self.bar:
            return
        x0 = px(PAD + LABEL_W)
        x1 = px(WIDTH - PAD - self.TOTAL_W - self.EXTRA_W * 2 - 8)
        h = px(14)
        d.rounded_rectangle([x0, cy - h // 2, x1, cy + h // 2], radius=px(3), fill=TRACK)
        parts = [(n, c) for n, c in self.parts if n]
        x = x0
        for i, (n, colour) in enumerate(parts):
            w = (x1 - x0) * n / self.scale if self.scale else 0
            right = x + w - (px(1.5) if i < len(parts) - 1 else 0)
            d.rounded_rectangle([x, cy - h // 2, max(right, x + px(2)), cy + h // 2],
                                radius=px(3), fill=colour)
            x += w
        right = px(WIDTH - PAD - self.EXTRA_W * 2)
        d.text((right, cy), self.total_text, font=font(15, True),
               fill=INK if parts else MUTED, anchor="rm")
