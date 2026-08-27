"""Geometry and type styling for the card face.

Transcribed from `scenes/cards/card.tscn` in the azoth repo. Every node there is
anchored to the viewport centre (anchors_preset = 8) with offsets in pixels, so
each box below is `centre + offset`, resolved once here rather than at each draw
site.

**When the card template changes, this file is what goes stale.** The asset sync
script cannot detect layout edits -- it only copies art. See
docs/CARD_RENDERING.md for the update checklist.
"""
from __future__ import annotations

# SubViewport size in card.tscn. Everything below is in this space.
CARD_W, CARD_H = 560, 897
CX, CY = CARD_W / 2, CARD_H / 2          # 280.0, 448.5


def _box(left, top, right, bottom):
    """A node's (x, y, w, h) from its card.tscn offsets around the centre."""
    x0, y0 = CX + left, CY + top
    return (x0, y0, (CX + right) - x0, (CY + bottom) - y0)


# --- Node boxes, in card.tscn declaration order -----------------------------
BACKGROUND = _box(-300, -409, 300, 406)     # Background TextureRect
BORDER     = _box(-300, -409, 300, 407)     # PreviouslyImage -- the element border
ART        = _box(-137, -129, 138, 146)     # Image -- 275x275, the EXR/PNG art
SYMBOL     = _box(-270, -368.5, -202, -300.5)   # Symbol -- 68x68, top-left corner
TITLE_BOX  = _box(-175, -348.5, 175, -162.5)    # VBoxContainer: name + subtype
TEXT_BOX   = _box(-206, 251.5, 194, 307.5)      # TextLabel -- rules text

# ValenceLabel is a child of Symbol, so its offsets are relative to SYMBOL.
VALENCE_REL = (15.667, 2.0, 41.0, 57.0)     # x, y, w, h within SYMBOL
# Second_ValenceLabel, for split cards (phase 2). Offsets are relative to Symbol
# but place it on the opposite corner.
VALENCE2_REL = (484.0, 2.333, 41.0, 60.0)

# --- Type styling ----------------------------------------------------------
FONT_FILE = "Aldrich-Regular.ttf"

# Godot's `outline_size` and PIL's `stroke_width` are NOT the same unit.
#
# card.tscn sets outline_size = 3 on both the name and the valence. Passing 3 to
# PIL gives a far heavier glyph: measured on the valence "2" at size 60, Godot
# renders 33x44 with 908 filled pixels, PIL at stroke_width=3 renders 37x48 with
# 1405 -- roughly 55% more ink, enough to fill in the counters and read as bold.
#
# PIL stroke_width=1 reproduces it almost exactly: 33x44 / 906 for "2", and
# 32x44 / 974 against Godot's 971 for "6". The name calibrates to the same
# value (227px wide in both).
GODOT_OUTLINE_3 = 1

NAME_SIZE = 55          # NameLabel, theme_override_font_sizes/font_size
NAME_COLOR = (245, 245, 245)                # Color(0.960784, ...)
NAME_OUTLINE = GODOT_OUTLINE_3
NAME_OUTLINE_COLOR = (255, 255, 255)        # explicitly overridden in card.tscn

SUBTYPE_SIZE = 35       # TypeLabel normal_font_size
TITLE_SEPARATION = 15   # VBoxContainer theme_override_constants/separation

TEXT_SIZE = 40          # TextLabel normal_font_size
TEXT_COLOR = (255, 255, 255)

VALENCE_SIZE = 60       # ValenceLabel font_size
VALENCE_COLOR = (0, 0, 0)
VALENCE_OUTLINE = GODOT_OUTLINE_3
# ValenceLabel sets outline_size but NOT font_outline_color, so it takes Godot's
# theme default -- black. Black text with a black outline reads as bold, not as
# an outline. (NameLabel is the opposite: it overrides the colour to white.)
VALENCE_OUTLINE_COLOR = (0, 0, 0)

# Godot lays text out ~2.6% wider than PIL does for the same font and size.
# Measured on the rules line "per card drawn" at size 40: Godot 321px, PIL
# 312.8px. Cap heights match exactly, so this is advance/spacing, not scale.
#
# It only matters for WRAPPING: a line that just fits in PIL can overflow in
# Godot, so break points drift and the card reads differently. Shrinking the
# wrap width by the same ratio reproduces Godot's breaks without touching how
# the glyphs are drawn.
GODOT_ADVANCE_RATIO = 1.026


def wrap_width(box_width: float) -> float:
    """Usable width for wrapping, corrected for Godot's wider advance."""
    return box_width / GODOT_ADVANCE_RATIO

# Element colours, matching GlobalVars / the game's symbol tints. Used for the
# subtype line and the art's accent zone.
ELEMENT_COLORS = {
    "blood": (255, 0, 0),
    "sol": (249, 164, 16),
    "anima": (135, 105, 233),
}
DEFAULT_ELEMENT_COLOR = (255, 255, 255)

# Border file per element, from BaseCard.border_paths. A null element -> default.
BORDER_FILES = {
    "default": "blurred_white_border_noline.png",
    "anima": "blurred_anima_border_noline.png",
    "blood": "blurred_blood_border_noline.png",
    "sol": "blurred_sol_border_noline.png",
}
BACKGROUND_FILE = "blurred_card_background2.png"

# A split card draws a SECOND border over the first. BaseCard.border_paths keys
# these "split_<element>", falling back to the default border for anything else.
SPLIT_BORDER_FILES = {
    "anima": "blurred_second_anima_border3.png",
    "blood": "blurred_second_blood_border3.png",
    "sol": "blurred_second_sol_border3.png",
}


def split_border_file(element):
    """The overlay border for a split card's second face, or None."""
    return SPLIT_BORDER_FILES.get((element or "").lower())


def split_face(card):
    """The split face as (element, valence), or None for a single-sided card.

    Guarded with an explicit dict/non-empty check: every card exported from the
    database carries "split": null, so a bare key check is true for every card.
    """
    split = card.get("split")
    if isinstance(split, dict) and split:
        return str(split.get("element") or ""), split.get("valence")
    return None


def element_color(element):
    return ELEMENT_COLORS.get((element or "").lower(), DEFAULT_ELEMENT_COLOR)


def border_file(element):
    key = (element or "default").lower()
    return BORDER_FILES.get(key, BORDER_FILES["default"])
