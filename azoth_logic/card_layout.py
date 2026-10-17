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
# TextLabel, a TypesetLabel since 2026-09-27: the space the text is centred in.
# Top is the art's bottom; the bottom is clipped to the card's visible
# silhouette (BaseCard.silhouette_viewport_rect, y 64.5 -> 829.5), then both are
# inset by `clearance` (TypesetLabel's default, 16). Centred on the viewport.
TEXT_REGION = _box(-220, 146, 220, 406)
SILHOUETTE_BOTTOM = 829.5
TEXT_CLEARANCE = 16.0
# card.tscn's TextLabel keeps this from the bottom lobes (lobe_reference =
# PreviouslyImage, whose box is the border's own 600x816 pixel space).
TEXT_LOBE_CLEARANCE = 11.0


def text_region():
    """TextLabel's typesetter region, lobes included."""
    from azoth_logic.typesetter import Region
    x, y, w, h = TEXT_REGION
    return Region(x, y, x + w, min(y + h, SILHOUETTE_BOTTOM), clearance=TEXT_CLEARANCE,
                  lobes=True, to_border=(-BORDER[0], -BORDER[1]),
                  lobe_clearance=TEXT_LOBE_CLEARANCE)

# The corner badges hang off the card's left edge, past the viewport -- the
# game draws them in base_card_shader, where nothing in the SubViewport reaches.
# Both reference nodes are anchors_preset 0: offsets from the viewport ORIGIN,
# not its centre.
DAMAGE_BADGE = (-5.0, 223.5, 88.0, 69.0)             # Damage_Badge: left edge, v-centre
MAGNIFY_BADGE = (-47.333344, 200.33333, 124.913974, 112.66667)   # Magnify_Badge
# BaseCard: the damage pointer is this x the lone escalate pointer's height.
DAMAGE_BADGE_SCALE = 1.3
# BaseCard.MAGNIFY_BELOW_DAMAGE_GAP: between the pointer and Magnify's body.
MAGNIFY_BELOW_DAMAGE_GAP = 10.0
# BaseCard.BADGE_CIRCLE_IN_ICON: Magnify's round body in its glyph, as fractions
# of the glyph's width (x, y, radius).
BADGE_CIRCLE_IN_ICON = (26.48 / 51.0, 26.0 / 51.0, 22.12 / 51.0)
# How far left of the viewport a badge can reach, plus a margin: the canvas a
# badged card is drawn on is this much wider.
BADGE_PAD = 56

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

TEXT_COLOR = (255, 255, 255)

VALENCE_SIZE = 60       # ValenceLabel font_size
VALENCE_COLOR = (0, 0, 0)
VALENCE_OUTLINE = GODOT_OUTLINE_3
# ValenceLabel sets outline_size but NOT font_outline_color, so it takes Godot's
# theme default -- black. Black text with a black outline reads as bold, not as
# an outline. (NameLabel is the opposite: it overrides the colour to white.)
VALENCE_OUTLINE_COLOR = (0, 0, 0)

# Godot's RichTextLabel laid text out ~2.6% wider than PIL does for the same
# font and size. Measured on the rules line "per card drawn" at size 40: Godot
# 321px, PIL 312.8px. Cap heights match exactly, so this is advance/spacing, not
# scale. Used now only for the name and subtype: rules text goes through the
# typesetter, which measures with Font.get_string_size and agrees with PIL
# uncorrected (azoth_logic/typesetter.py).
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
# border blobs, the subtype line, the art's accent zone and the damage badge.
ELEMENT_COLORS = {
    "blood": (255, 0, 0),
    "sol": (249, 164, 16),
    "anima": (135, 105, 233),
}
DEFAULT_ELEMENT_COLOR = (255, 255, 255)

BACKGROUND_FILE = "blurred_card_background2.png"
# card_border_dim.gdshader's dim_color: the card body painted under the blobs.
FACE_COLOR = (22, 22, 22)


def is_colorless(card) -> bool:
    """No element: a catalyst or a boss reward. The game paints its border
    blobs in a rainbow and shows no valence corner
    (BaseCard._apply_colorless_visuals)."""
    return not (card.get("element") or "")


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
