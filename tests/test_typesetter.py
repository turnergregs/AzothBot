"""The port of the game's typesetter: size, breaks, placement, lobe margins.

The reference is the game itself: tests/fixtures/typeset_reference.json holds
what the game's typesetter chose for every shipped text (made with the azoth
repo's tools/CardRenderTool.tscn --print; see docs/CARD_RENDERING.md).
"""
import json
from pathlib import Path

import pytest

from azoth_logic import card_layout as L, fate_layout as F, inline_icons as I, rich_text
from azoth_logic import typesetter as T

REFERENCE = Path(__file__).parent / "fixtures" / "typeset_reference.json"


def _region(kind):
    if kind == "card":
        return L.text_region()
    box = F.RITE_TEXT if kind == "rite" else F.ASPECT_TEXT
    clearance = F.RITE_TEXT_CLEARANCE if kind == "rite" else F.ASPECT_TEXT_CLEARANCE
    x, y, w, h = box
    return T.Region(x, y, x + w, min(y + h, F.SILHOUETTE_BOTTOM), clearance=clearance)


def _lines(laid):
    return [" ".join("".join("@" if a[0] == "img" else a[1] for a in ln).split())
            for ln, _, _ in laid.lines]


def _norm(line):
    return " ".join(line.replace(" ,", ",").replace(" :", ":").replace(" .", ".")
                    .replace(' "', '"').split())


def test_the_port_chooses_what_the_game_chose():
    """Size and breaks, over every shipped text. 222 of 223 matched when the
    port was written (2026-10-17); the one miss was a size one point apart.
    Fewer than that means the port or the game's typesetter moved."""
    texts = json.loads(REFERENCE.read_text())["texts"]
    misses = []
    for entry in texts:
        laid = rich_text.typeset(entry["text"], _region(entry["kind"]))
        mine = [_norm(line) for line in _lines(laid)]
        if laid.size != entry["size"] or mine != [_norm(x) for x in entry["lines"]]:
            misses.append((entry["text"], entry["size"], entry["lines"], laid.size, mine))
    assert len(misses) <= 1, misses[:5]


def test_one_line_is_centred_on_its_ink():
    region = _region("aspect")
    laid = rich_text.typeset("Draw three", region)
    middle = (region.band_top + region.band_bottom) / 2
    assert (laid.ink_top + laid.ink_bottom) / 2 == pytest.approx(middle, abs=0.01)


def test_a_lone_icon_is_set_larger():
    laid = rich_text.typeset("[2bonus]", _region("card"))
    assert laid.size == round(T.PREFERRED_SIZE * I.LONE_ICON_SCALE)
    assert rich_text.typeset("[2bonus]", _region("card"), lone_icon=False).size == T.PREFERRED_SIZE


def test_the_lobes_narrow_the_lower_lines():
    region = _region("card")
    top = region.width_between(region.band_top, region.band_top + 30)
    bottom = region.width_between(region.band_bottom - 30, region.band_bottom)
    assert top == pytest.approx(440.0)
    assert bottom < top


def test_a_lower_row_never_gets_room_back():
    """Running minimum from the top: no line reaches into the notch where the
    two lobes meet."""
    table = T._lobe_table(11.0)
    assert all(b <= a for a, b in zip(table, table[1:]))


def test_text_that_cannot_fit_spills_at_the_smallest_size():
    region = T.Region(0, 0, 200, 60, clearance=4)
    laid = rich_text.typeset("word " * 40, region)
    assert laid.size == T.MIN_SIZE and not laid.fits


def test_clinging_words_do_not_end_a_line():
    costs = T._measure(rich_text._words(rich_text._atoms(
        rich_text.tokenize("Draw a card per turn"), I.font(40))), 40)
    plain = [w["plain"] for w in costs["words"]]
    assert costs["break_after"][plain.index("a")] > costs["break_after"][plain.index("card")]
