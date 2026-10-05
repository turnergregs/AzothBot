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
import math
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
            # Whole pixels: PIL's rounded_rectangle raises for a bar between
            # one and two pixels wider than its own height, which a fractional
            # end lands in for some small values (a 1/17 share, 2026-10-04).
            x = round(bar_x0 + (bar_x1 - bar_x0) * min(max(self.value, 0), 1))
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
    """A report image: a title, a subtitle saying what it rests on, blocks.

    A subtitle may run to more than one line ("\\n"); each adds SUBTITLE_LINE.
    `thumb` is an image (already at SCALE) drawn in the top-right corner, for
    a report about one thing, so the reader sees WHICH thing. Blocks do not
    flow around it: a caller that puts a block beside it insets that block
    (StatTiles' `inset_right`) and starts full-width blocks below it.
    """
    title: str
    subtitle: str = ""
    blocks: list = field(default_factory=list)
    thumb: object = None

    HEAD = 64
    SUBTITLE_LINE = 18

    def add(self, *blocks: Block) -> "Card":
        self.blocks.extend(blocks)
        return self

    def head(self) -> float:
        return self.HEAD + self.SUBTITLE_LINE * self.subtitle.count("\n")

    def render(self) -> Image.Image:
        height = PAD + self.head() + sum(b.height for b in self.blocks) + PAD
        if self.thumb is not None:
            height = max(height, PAD + self.thumb.height / SCALE + PAD)
        img = Image.new("RGBA", (px(WIDTH), px(height)), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=px(12), fill=SURFACE)
        if self.thumb is not None:
            img.alpha_composite(self.thumb, (px(WIDTH - PAD) - self.thumb.width, px(PAD)))
        d.text((px(PAD), px(PAD)), self.title, font=font(22, True), fill=INK)
        for i, line in enumerate(self.subtitle.split("\n") if self.subtitle else []):
            d.text((px(PAD), px(PAD + 30 + self.SUBTITLE_LINE * i)), line, font=font(13), fill=INK_2)
        top = px(PAD + self.head())
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
    indent: float = LABEL_W    # from the card's padding; 0 under a chart with no label column

    def draw(self, d, top):
        cy = top + px(self.height) // 2
        x = px(PAD + self.indent)
        for label, colour, *shape in self.items:
            if shape and shape[0] == "line":     # the key to a reference tick
                d.line([(x - px(2), cy), (x + px(12), cy)], fill=colour, width=px(2))
                x += px(16)
            else:
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
    inset_right: float = 0     # room left clear on the right (a Card's thumb)

    def draw(self, d, top):
        w = (WIDTH - PAD * 2 - self.inset_right) / self.columns
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


@dataclass
class TableRow(Block):
    """One row of a plain table: `[(text, x_points, anchor, style)]`.

    For ranked lists where the number IS the point and a bar would mislead: a
    leaderboard's combos grow exponentially, so any bar is one giant and nine
    slivers. Styles: "strong" (primary, bold), "normal" (secondary),
    "muted" (small, muted), "rank" (small, bold, muted).
    """
    cells: list
    height: float = 30

    STYLES = {"strong": (15, True, INK), "normal": (15, False, INK_2),
              "muted": (13, False, MUTED), "rank": (13, True, MUTED)}

    def draw(self, d, top):
        cy = top + px(self.height) // 2
        for text, x, anchor, style in self.cells:
            size, bold, fill = self.STYLES[style]
            d.text((px(x), cy), text, font=font(size, bold), fill=fill, anchor=anchor)


@dataclass
class RateColumns(Block):
    """One column per group over an ordered axis (versions, ritual levels),
    each with a horizontal tick at its OWN baseline: a trend read left to
    right, where what a column is read against changes from group to group.

    `groups`: dicts with `label`, `value` (0-1), `rest` (the baseline, 0-1 or
    None), `count_text` (under the label), and optionally `faded` and
    `marker` / `marker_fill` for a flag, drawn beside the value. The column
    keeps `fill` (the item's own colour) whether flagged or not, so the flag
    is carried by the marker; a faded column is an outline in that colour.

    Slots are capped at SLOT_MAX wide and centred, so three versions do not
    stretch into three slabs; past a width's worth the caller trims. The scale
    tops out at the next 25% above the tallest mark (50% at the least),
    labelled in a right margin no column enters: rates mostly sit under 60%,
    and a fixed 0-100% left half of every chart empty.

    `baseline`, in place of each group's `rest`, is ONE line across the plot
    for groups all read against the same rate (a section's overall pick rate).
    A tick per column at the same height reads as a row of dashes.
    """
    groups: list
    fill: str = ACCENT
    baseline: float | None = None
    height: float = 180

    PLOT_H = 104      # the height of a column at the top of the scale
    TOP = 34          # room above a full column for its value
    AXIS_W = 30       # the right margin the gridline labels sit in
    SLOT_MAX = 84
    COLUMN_W = 30
    TICK_OVER = 7     # how far the baseline tick reaches past each side
    LABEL_CLEAR = 20  # a tick this far above a column would cut its value

    def scale(self) -> float:
        tallest = max([v for g in self.groups for v in (g.get("value"), g.get("rest"))
                       if v is not None] + [self.baseline or 0], default=0)
        return min(max(0.5, math.ceil(tallest * 4 - 1e-9) / 4), 1.0)

    def draw(self, d, top):
        n = max(len(self.groups), 1)
        inner = WIDTH - PAD * 2 - self.AXIS_W
        slot = min(self.SLOT_MAX, inner / n)
        x0 = PAD + (inner - slot * n) / 2
        base = top + px(self.TOP + self.PLOT_H)
        col_w = min(self.COLUMN_W, slot * 0.45)
        scale = self.scale()
        right = px(WIDTH - PAD - self.AXIS_W + 6)
        for step in range(1, round(scale * 4) + 1):   # quiet gridlines every 25%
            y = base - px(self.PLOT_H * step / 4 / scale)
            d.line([(px(PAD), y), (right, y)], fill=TRACK, width=px(0.5))
            d.text((px(WIDTH - PAD), y), f"{step * 25}%", font=font(10), fill=MUTED, anchor="rm")
        d.line([(px(PAD), base), (right, base)], fill=MUTED, width=px(0.75))
        label_font = font(13 if slot >= 48 else 11)

        def height_of(value):
            return self.PLOT_H * min(max(value, 0), scale) / scale

        if self.baseline is not None:
            y = base - px(height_of(self.baseline))
            d.line([(px(PAD), y), (right, y)], fill=REFERENCE, width=px(1))

        for i, g in enumerate(self.groups):
            centre = x0 + slot * (i + 0.5)
            faded = g.get("faded", False)
            value, rest = g.get("value"), g.get("rest")
            # A shared baseline runs behind every column and breaks round each
            # value instead (below), so it never lifts a value off its column.
            line = rest
            x_a, x_b = px(centre - col_w / 2), px(centre + col_w / 2)
            peak = 0
            if value is not None:
                h = max(height_of(value), 2)
                peak = h
                # The value sits on its column. Only a line that would cut
                # through the number (just above the column) lifts it over.
                if line is not None and 0 <= height_of(line) - h <= self.LABEL_CLEAR:
                    peak = height_of(line)
                box = [x_a, base - px(h), x_b, base]
                if faded:
                    d.rounded_rectangle(box, radius=px(3), outline=dim(self.fill, 0.75), width=px(1.25))
                else:
                    d.rounded_rectangle(box, radius=px(3), fill=self.fill)
            if rest is not None:
                y = base - px(height_of(rest))
                # A dark keyline under the tick so it reads over any fill.
                d.line([(x_a - px(self.TICK_OVER), y), (x_b + px(self.TICK_OVER), y)],
                       fill=SURFACE, width=px(4))
                d.line([(x_a - px(self.TICK_OVER), y), (x_b + px(self.TICK_OVER), y)],
                       fill=REFERENCE, width=px(2))
            if value is not None:
                text = f"{round(value * 100)}"
                text_y = base - px(peak) - px(6)
                f = font(13, True)
                if self.baseline is not None:
                    box = d.textbbox((px(centre), text_y), text, font=f, anchor="mb")
                    d.rectangle([box[0] - px(3), box[1] - px(2), box[2] + px(3), box[3] + px(2)],
                                fill=SURFACE)
                d.text((px(centre), text_y), text, font=f, fill=INK_2 if faded else INK, anchor="mb")
                if g.get("marker"):
                    x = px(centre) - d.textlength(text, font=f) / 2 - px(3)
                    d.text((x, text_y), g["marker"], font=font(10),
                           fill=g.get("marker_fill") or INK_2, anchor="rb")
            d.text((px(centre), base + px(16)), str(g.get("label", "")), font=label_font,
                   fill=INK_2 if faded else INK, anchor="mm")
            if g.get("count_text"):
                d.text((px(centre), base + px(33)), g["count_text"], font=font(11),
                       fill=MUTED, anchor="mm")


@dataclass
class Histogram(Block):
    """A distribution over whole numbers: one column per value, left to right,
    its height the share of the total. For "how many X did a turn hold".

    `counts[i]` is how many times the value i occurred; zeros between the first
    and last are kept, since a gap in a distribution is information. Up to
    LABEL_ALL columns each carries its share; past that only the tallest does
    and the gridlines (labelled in a right margin, as RateColumns) carry the
    rest, and the axis is numbered every AXIS_EVERY. A value that occurred at
    all is at least MIN_H tall, so a long tail of single turns stays visible.
    `mean` draws a reference line at that position, labelled above the plot.

    `labels` names the columns when they are not 0, 1, 2... (a "—" column for
    cards with no valence ahead of 1-10). `show="count"` labels each column
    with its count rather than its share, for content (how many cards), where
    a count is the number that matters; the gridlines, which are shares, go.
    `label_all` overrides the LABEL_ALL rule.
    """
    counts: list
    mean: float | None = None
    labels: list | None = None
    show: str = "share"
    label_all: bool | None = None
    fill: str = NEUTRAL
    height: float = 170

    PLOT_H = 104
    TOP = 34          # room above the plot for a column's share, and the mean
    AXIS_W = 30
    COLUMN_MAX = 24   # a column never fills a wide slot: the rest is air
    GAP = 2           # surface between neighbouring columns
    MIN_H = 2
    LABEL_ALL = 10
    AXIS_EVERY = 5

    def scale(self) -> float:
        """The top gridline: the tallest share rounded up to the step."""
        total = sum(self.counts) or 1
        tallest = max(self.counts, default=0) / total
        step = self.step()
        return max(step, math.ceil(tallest / step - 1e-9) * step)

    def step(self) -> float:
        total = sum(self.counts) or 1
        tallest = max(self.counts, default=0) / total
        return 0.1 if tallest > 0.25 else 0.05

    def draw(self, d, top):
        n = max(len(self.counts), 1)
        total = sum(self.counts) or 1
        inner = WIDTH - PAD * 2 - self.AXIS_W
        slot = inner / n
        col_w = min(self.COLUMN_MAX, slot - self.GAP)
        base = top + px(self.TOP + self.PLOT_H)
        right = px(WIDTH - PAD - self.AXIS_W + 6)
        scale, step = self.scale(), self.step()

        for k in range(1, round(scale / step) + 1 if self.show == "share" else 1):
            y = base - px(self.PLOT_H * k * step / scale)
            d.line([(px(PAD), y), (right, y)], fill=TRACK, width=px(0.5))
            d.text((px(WIDTH - PAD), y), f"{round(k * step * 100)}%", font=font(10),
                   fill=MUTED, anchor="rm")

        def x_of(value: float) -> float:
            return PAD + slot * (value + 0.5)

        tallest = max(range(n), key=lambda i: self.counts[i]) if self.counts else None
        label_all = n <= self.LABEL_ALL if self.label_all is None else self.label_all
        for i, c in enumerate(self.counts):
            centre = x_of(i)
            if c:
                h = max(self.PLOT_H * (c / total) / scale, self.MIN_H)
                d.rounded_rectangle([px(centre - col_w / 2), base - px(h),
                                     px(centre + col_w / 2), base],
                                    radius=px(min(3, col_w / 2, h / 2)), fill=self.fill,
                                    corners=(True, True, False, False))
                if label_all or i == tallest:
                    pct = round(100 * c / total)
                    text = str(c) if self.show == "count" else f"{pct}%" if pct else "<1%"
                    d.text((px(centre), base - px(h) - px(5)), text,
                           font=font(12 if label_all else 11, True), fill=INK, anchor="mb")
            if label_all or i % self.AXIS_EVERY == 0:
                name = self.labels[i] if self.labels else str(i)
                d.text((px(centre), base + px(12)), name, font=font(12 if label_all else 11),
                       fill=INK_2, anchor="mm")
        d.line([(px(PAD), base), (right, base)], fill=MUTED, width=px(0.75))

        if self.mean is not None:
            x = px(x_of(self.mean))
            y_top = top + px(self.TOP - 6)
            d.line([(x, y_top), (x, base)], fill=SURFACE, width=px(3))
            d.line([(x, y_top), (x, base)], fill=REFERENCE, width=px(1))
            # Beside the line's top, on the side away from the tallest
            # column, whose share is labelled at about the same height.
            away_left = tallest is not None and tallest >= self.mean
            d.text((x + px(-4 if away_left else 4), y_top), f"avg {self.mean:.1f}",
                   font=font(11), fill=INK_2, anchor="rt" if away_left else "lt")
