"""Where rules text breaks, what size it is set at, and where it sits.

A port of the game's `Typesetter` (azoth `scripts/helpers/typesetter.gd`,
`docs/TYPESETTER.md`) and the `CardLobeMargin` it reads, minus parsing and
drawing: words come from `rich_text`, and `rich_text.draw_typeset` draws.

For every size from 32 to 46 (cheapest size cost first, stopping once a size's
cost alone cannot win) and every line count from the fewest that fit to two
more: place the empty block centred so each line's available width is known,
break each paragraph with a dynamic program over its gaps, place the result with
its real ink and check every line against the width where it landed, then add
the zigzag and size costs. The cheapest wins; if nothing fits, the smallest size
spills. A text that is one icon alone skips the search at 1.33 x the size.

On a card face each line's width is also narrowed to keep `lobe_clearance`
(plus the border's breathing) from the two bottom-right lobes, read from the
same signed distance fields `card_render` paints the border with
(`assets/card_art/border_blobs.json`). A running minimum from the top means a
lower line never gets room back.

Widths are PIL's, uncorrected: the typesetter measures with
`Font.get_string_size`, which agrees with PIL (see `inline_icons`), so
`card_layout.GODOT_ADVANCE_RATIO` (the old RichTextLabel's) does not apply.

Checked against the game: every shipped card, aspect and rite text typeset by
the game's `tools/CardRenderTool.tscn --print` and by this port chose the same
size and the same breaks for 222 of 223 (the rest carry run-time tokens).

Every weight is the game's, by the same name. Keep them in step by hand.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from azoth_logic import inline_icons as I

# --- Style (TypesetLabel exports on card.tscn / aspect_card / rite_card) -------
PREFERRED_SIZE = 40
MIN_SIZE = 32
MAX_SIZE = 46
LINE_HEIGHT = 1.2
MAX_EXTRA_LINES = 2
ICON_GAP = 0.12

# --- Costs -------------------------------------------------------------------
BALANCE_WEIGHT = 2.5
FILL_WEIGHT = 0.25
WIDOW_WEIGHT = 0.35
DETACHED_WEIGHT = 0.3
CLINGING_WEIGHT = 0.3
BREAK_WEIGHT = 0.25
STOP_BONUS = 0.25
COMMA_BONUS = 0.12
ZIGZAG_WEIGHT = 1.0
SIZE_WEIGHT = 20.0
SMALLER_MULTIPLIER = 4.0
LARGER_MULTIPLIER = 6.0
OVERFLOW_WEIGHT = 100.0

CLINGING_WORDS = frozenset([
    "a", "an", "the", "of", "or", "and", "in", "if", "on", "at", "by", "for",
    "from", "into", "with", "than", "per", "up", "this", "that", "next",
    "every", "all", "any", "its", "your", "their", "other", "&", "||",
])
BREAK_AFTER = ",.;:"
DESCENDERS = "gjpqy,;()|"
ATTACHED_STARTS = "0123456789#+-%"

UNREACHABLE = 1e30

# --- CardLobeMargin -------------------------------------------------------------
BLOBS_FILE = Path(__file__).resolve().parent.parent / "assets" / "card_art" / "border_blobs.json"
BOTTOM_LOBES = (2, 3)
ROW_STEP = 2.0
REACH_PRECISION = 0.25
# card_border_dim.gdshader: motion_amount x (1 + WAVE_SHARE), how far a blob's
# edge can breathe toward the text. Keep in step with the shader by hand.
BREATHING_PX = 2.5 * (1.0 + 0.35)


@lru_cache(maxsize=1)
def _border():
    return json.loads(BLOBS_FILE.read_text())["border"]


def _lobe_distance(x: float, y: float) -> float:
    blobs = _border()["blobs"]
    d = math.inf
    for index in BOTTOM_LOBES:
        blob = blobs[index]
        values = [x * a + y * b - o for a, b, o in blob["planes"]]
        m = max(values)
        k = blob["k"]
        d = min(d, m + k * math.log(sum(math.exp((v - m) / k) for v in values)))
    return d


@lru_cache(maxsize=8)
def _lobe_table(clearance: float) -> tuple:
    """Per ROW_STEP border pixels down the card, the half-width a line may
    reach from the centre line (CardLobeMargin._table_for)."""
    width, height = _border()["size"]
    keep_out = round(clearance * 2) / 2 + BREATHING_PX
    centre = width * 0.5
    rows = int(math.ceil(height / ROW_STEP)) + 1
    running = centre
    table = []
    for row in range(rows):
        y = row * ROW_STEP
        running = min(running, _reach(centre, y, keep_out, width))
        table.append(running)
    return tuple(table)


def _reach(centre: float, y: float, keep_out: float, width: float) -> float:
    if _lobe_distance(width, y) >= keep_out:
        return width - centre
    if _lobe_distance(centre, y) < keep_out:
        return 0.0
    inside, outside = centre, width
    while outside - inside > REACH_PRECISION:
        mid = (inside + outside) * 0.5
        if _lobe_distance(mid, y) < keep_out:
            outside = mid
        else:
            inside = mid
    return inside - centre


def lobe_half_width(y_top: float, y_bottom: float, clearance: float) -> float:
    """The narrowest reach across a band, in border pixels."""
    table = _lobe_table(clearance)
    first = min(max(int(math.floor(y_top / ROW_STEP)), 0), len(table) - 1)
    last = min(max(int(math.ceil(y_bottom / ROW_STEP)), 0), len(table) - 1)
    return min(table[first:last + 1])


@dataclass
class Region:
    """The space text is centred in, in viewport pixels (Typesetter.Region).

    `x0..x1` is the text column; `y0..y1` the free space, already clipped to the
    card's silhouette; `clearance` the ink's inset from top and bottom. With
    `lobes`, `to_border` is the (dx, dy) from viewport to border pixels (the
    border box is 1:1) and lines keep `lobe_clearance` from the bottom lobes,
    centred on the border's centre line.
    """
    x0: float
    y0: float
    x1: float
    y1: float
    clearance: float = 16.0
    lobes: bool = False
    to_border: tuple = (0.0, 0.0)
    lobe_clearance: float = 16.0

    @property
    def centre_x(self) -> float:
        if self.lobes:
            return _border()["size"][0] * 0.5 - self.to_border[0]
        return (self.x0 + self.x1) * 0.5

    @property
    def band_top(self) -> float:
        return self.y0 + self.clearance

    @property
    def band_bottom(self) -> float:
        return self.y1 - self.clearance

    def width_between(self, y_top: float, y_bottom: float) -> float:
        centre = self.centre_x
        half = min(centre - self.x0, self.x1 - centre)
        if self.lobes:
            dy = self.to_border[1]
            half = min(half, lobe_half_width(min(y_top, y_bottom) + dy,
                                             max(y_top, y_bottom) + dy, self.lobe_clearance))
        return max(half * 2.0, 0.0)


# ---------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------

def _measure(paragraphs, size: int) -> dict:
    """Flatten rich_text's paragraphs into the word list the breaker wants, with
    each word's facts and per-word costs (Typesetter._flatten / _measure)."""
    cap, desc = I.cap_height(size), I.descender(size)
    icon_mid = I.ICON_CENTRE * cap
    words, ranges, para_start = [], [], []
    for para in paragraphs:
        start = len(words)
        if not para:
            words.append({"atoms": [], "space": 0.0, "width": 0.0, "plain": "", "has_text": False,
                          "descends": False, "attached": False, "ends": False, "blank": True,
                          "reach": (0.0, 0.0)})
        for atoms, space in para:
            texts = [a[1] for a in atoms if a[0] in ("word", "bold")]
            plain = "".join(texts)
            up = down = 0.0
            for a in atoms:
                if a[0] == "img":
                    u, d = a[1].reach(icon_mid)
                    up, down = max(up, u), max(down, d)
            first, last = atoms[0], atoms[-1]
            words.append({
                "atoms": atoms, "space": space,
                "width": sum(a[2] for a in atoms),
                "plain": plain, "has_text": bool(texts),
                "descends": any(c in DESCENDERS for c in plain),
                "attached": first[0] == "img" or (first[0] in ("word", "bold")
                                                  and first[1][:1] in ATTACHED_STARTS),
                "ends": last[0] in ("word", "bold") and last[1][-1:] in BREAK_AFTER,
                "blank": False, "reach": (up, down),
            })
        para_start.extend([start] * (len(words) - start))
        ranges.append((start, len(words)))
    break_after, alone = [], []
    for w, word in enumerate(words):
        break_after.append(_break_cost(word, words[w + 1]) if w + 1 < len(words) else 0.0)
        alone.append(0.0 if word["ends"] and not word["plain"].endswith(",") else WIDOW_WEIGHT)
    space = I.font(size).getlength(" ")
    icon_up = max((w["reach"][0] for w in words), default=0.0)
    icon_down = max((w["reach"][1] for w in words), default=0.0)
    return {
        "words": words, "ranges": ranges, "para_start": para_start,
        "break_after": break_after, "alone": alone, "space": space,
        "cap": cap, "desc": desc, "pitch": size * LINE_HEIGHT,
        "top_c": max(cap, icon_up),
        "bottom_c": max(desc if any(w["descends"] for w in words) else 0.0, icon_down),
    }


def _break_cost(before: dict, after: dict) -> float:
    if before["ends"]:
        discount = COMMA_BONUS if before["plain"].endswith(",") else STOP_BONUS
        return max(BREAK_WEIGHT - discount, 0.0)
    cost = BREAK_WEIGHT
    if after["attached"]:
        cost += DETACHED_WEIGHT
    if before["plain"].lower() in CLINGING_WORDS:
        cost += CLINGING_WEIGHT
    return cost


# ---------------------------------------------------------------------------
# Breaking
# ---------------------------------------------------------------------------

def _line_cost(ctx, i, j, pe, m, line_width, room, target) -> float:
    cost = 0.0
    if line_width > room + 0.5:
        cost += OVERFLOW_WEIGHT * (1.0 + (line_width - room) / max(room, 1.0))
    if m > 1:
        fill = min(max(line_width / max(room, 1.0), 0.0), 1.0)
        cost += BALANCE_WEIGHT * (fill - target) ** 2
        cost += FILL_WEIGHT * (1.0 - fill) ** 2
        if j - i == 1:
            cost += ctx["alone"][i]
    if j < pe:
        cost += ctx["break_after"][j - 1]
    return cost


def _break_paragraph(ctx, ps, pe, available, first_line, m):
    """One paragraph (words ps..pe) in exactly `m` lines from `first_line`:
    (cost, starts) or None."""
    words, space = ctx["words"], ctx["space"]
    n = pe - ps
    widths = [words[w]["width"] for w in range(ps, pe)]
    prefix = [0.0]
    for w in widths:
        prefix.append(prefix[-1] + w)
    target = 0.0
    if m > 1:
        room_total = sum(available[first_line:first_line + m])
        target = (prefix[n] + space * (n - m)) / max(room_total, 1.0)

    best = [[UNREACHABLE] * (n + 1) for _ in range(m + 1)]
    back = [[-1] * (n + 1) for _ in range(m + 1)]
    best[0][0] = 0.0
    for line in range(m):
        room = available[first_line + line]
        last_line = line == m - 1
        for i in range(n):
            so_far = best[line][i]
            if so_far >= UNREACHABLE:
                continue
            if last_line:
                rest = prefix[n] - prefix[i] + space * (n - i - 1)
                if rest > room + 0.5 and n > i + 1:
                    continue
                closing = so_far + _line_cost(ctx, ps + i, pe, pe, m, rest, room, target)
                if closing < best[m][n]:
                    best[m][n] = closing
                    back[m][n] = i
                continue
            for j in range(i + 1, n - (m - line - 1) + 1):
                line_width = prefix[j] - prefix[i] + space * (j - i - 1)
                if line_width > room + 0.5 and j > i + 1:
                    break
                total = so_far + _line_cost(ctx, ps + i, ps + j, pe, m, line_width, room, target)
                if total < best[line + 1][j]:
                    best[line + 1][j] = total
                    back[line + 1][j] = i
    if best[m][n] >= UNREACHABLE:
        return None
    starts, end = [], n
    for line in range(m, 0, -1):
        end = back[line][end]
        starts.append(ps + end)
    return best[m][n], starts[::-1]


def _break_lines(ctx, available, line_count):
    """Every paragraph's words in exactly `line_count` lines in all:
    (cost, starts) or None. The outer pass hands each paragraph a line count."""
    ranges = ctx["ranges"]
    count = len(ranges)
    best = [[UNREACHABLE] * (line_count + 1) for _ in range(count + 1)]
    chosen = [[None] * (line_count + 1) for _ in range(count + 1)]
    best[0][0] = 0.0
    for p, (ps, pe) in enumerate(ranges):
        later = count - p - 1
        for k in range(line_count + 1):
            so_far = best[p][k]
            if so_far >= UNREACHABLE:
                continue
            for m in range(1, min(pe - ps, line_count - k - later) + 1):
                inner = _break_paragraph(ctx, ps, pe, available, k, m)
                if inner is None:
                    continue
                total = so_far + inner[0]
                if total < best[p + 1][k + m]:
                    best[p + 1][k + m] = total
                    chosen[p + 1][k + m] = (k, inner[1])
    if best[count][line_count] >= UNREACHABLE:
        return None
    pieces, at = [], line_count
    for p in range(count, 0, -1):
        k, starts = chosen[p][at]
        pieces.insert(0, starts)
        at = k
    return best[count][line_count], [s for piece in pieces for s in piece]


# ---------------------------------------------------------------------------
# Placing
# ---------------------------------------------------------------------------

def _place(ctx, starts, region: Region, size: int):
    """The chosen lines placed on their real ink, re-centred, each checked
    against the width where it landed (Typesetter._place)."""
    words, space = ctx["words"], ctx["space"]
    cap, desc, pitch = ctx["cap"], ctx["desc"], ctx["pitch"]
    lines = []
    for k, first in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(words)
        width = top = bottom = 0.0
        for w in range(first, end):
            word = words[w]
            width += word["width"] + (space if w > first else 0.0)
            if word["has_text"] or word["blank"]:
                top = max(top, cap)
            if word["descends"]:
                bottom = max(bottom, desc)
            top = max(top, word["reach"][0])
            bottom = max(bottom, word["reach"][1])
        lines.append({"first": first, "end": end, "width": width, "top": top, "bottom": bottom})
    if not lines:
        return None
    baseline = 0.0
    for k, line in enumerate(lines):
        if k > 0:
            prev = lines[k - 1]
            baseline += max(pitch, prev["bottom"] + line["top"] + ICON_GAP * size)
        line["baseline"] = baseline
    ink_top = lines[0]["baseline"] - lines[0]["top"]
    ink_bottom = lines[-1]["baseline"] + lines[-1]["bottom"]
    shift = (region.band_top + region.band_bottom) * 0.5 - (ink_top + ink_bottom) * 0.5
    fits = ink_bottom - ink_top <= region.band_bottom - region.band_top + 0.5
    for line in lines:
        line["baseline"] += shift
        line["available"] = region.width_between(line["baseline"] - line["top"],
                                                 line["baseline"] + line["bottom"])
        if line["width"] > line["available"] + 0.5:
            fits = False
    return {"fits": fits, "lines": lines, "ink_top": ink_top + shift,
            "ink_bottom": ink_bottom + shift}


def _zigzag(ctx, starts, lines) -> float:
    para_start = ctx["para_start"]
    cost = 0.0
    for k in range(1, len(lines) - 1):
        if not (para_start[starts[k - 1]] == para_start[starts[k]] == para_start[starts[k + 1]]):
            continue
        fill = lambda line: line["width"] / max(line["available"], 1.0)
        dip = min(fill(lines[k - 1]), fill(lines[k + 1])) - fill(lines[k])
        if dip > 0.1:
            cost += ZIGZAG_WEIGHT * dip
    return cost


def size_cost(size: int) -> float:
    d = (size - PREFERRED_SIZE) / PREFERRED_SIZE
    return SIZE_WEIGHT * d * d * (SMALLER_MULTIPLIER if d < 0 else LARGER_MULTIPLIER)


def best_at_size(paragraphs, region: Region, size: int, allow_overflow: bool = False,
                 with_size_cost: bool = True):
    """The cheapest layout at one size over every line count, or None.
    Returns {"size", "cost", "fits", "lines": [{"first", "end", "baseline", ...}],
    "words", "ink_top", "ink_bottom"}."""
    ctx = _measure(paragraphs, size)
    words = ctx["words"]
    if not words:
        return None
    band = region.band_bottom - region.band_top
    band_mid = (region.band_top + region.band_bottom) * 0.5
    pitch, top_c, bottom_c = ctx["pitch"], ctx["top_c"], ctx["bottom_c"]
    best, fewest = None, -1
    for line_count in range(len(ctx["ranges"]), len(words) + 1):
        if fewest != -1 and line_count > fewest + MAX_EXTRA_LINES:
            break
        block = (line_count - 1) * pitch + top_c + bottom_c
        if block > band + 0.5 and not allow_overflow:
            break
        first = band_mid - block * 0.5 + top_c
        available = [region.width_between(first + k * pitch - top_c, first + k * pitch + bottom_c)
                     for k in range(line_count)]
        broken = _break_lines(ctx, available, line_count)
        if broken is None:
            continue
        placed = _place(ctx, broken[1], region, size)
        if placed is None or (not allow_overflow and not placed["fits"]):
            continue
        if fewest == -1:
            fewest = line_count
        cost = broken[0] + _zigzag(ctx, broken[1], placed["lines"])
        if with_size_cost:
            cost += size_cost(size)
        if best is None or cost < best["cost"]:
            best = dict(placed, cost=cost, size=size, words=words)
    return best
