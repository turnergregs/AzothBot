"""Geometry and styling for aspect and rite cards.

Transcribed from `scenes/cards/aspect_card.tscn` and `event_card.tscn` in the
azoth repo. Both use the same 560x897 viewport as `card.tscn`, so boxes resolve
the same way: centre + offset.

**"Rite" is the current name for what the database calls an "event".** The scene
file, the table and the `content_type` value all still say event; everything new
here says rite. See `azoth_commands/rites.py` for where that boundary sits.

Unlike a card, neither has an element border or a valence -- and their
backgrounds are procedural shaders rather than static art, which is why they are
vendored as pre-rendered PNGs.

Those PNGs are NOT produced by tools/sync_assets.py -- it copies files out of an
azoth checkout, and a shader has no file to copy. They are exported from a
running Godot by tools/BackgroundExportTool.tscn in the azoth repo; sync_assets
only verifies they are present. See docs/CARD_RENDERING.md.
"""
from __future__ import annotations

from azoth_logic.card_layout import CARD_W, CARD_H, CX, CY, FONT_FILE, wrap_width, GODOT_OUTLINE_3


def _box(left, top, right, bottom):
    x0, y0 = CX + left, CY + top
    return (x0, y0, (CX + right) - x0, (CY + bottom) - y0)


# The aspect and rite backgrounds are exported as full-VIEWPORT captures by
# tools/BackgroundExportTool.gd, so they already carry the card silhouette --
# rounded corners and all -- at its final position (x 8-551, y 69-827).
#
# That makes them UNLIKE a card's background, which is a raw texture file that
# has to be fitted into its node's box. Fitting these the same way stretches
# them to the node's 660x897 and pushes the rounded edges off-canvas, which is
# exactly what made rendered aspects and rites look square-cornered.
#
# So: composite at the origin, at native size. The node box below is kept for
# reference -- it is what the scene declares -- but it is not what to draw into.
BACKGROUND_FULL = (0.0, 0.0, float(CARD_W), float(CARD_H))

# --- Aspect ----------------------------------------------------------------
ASPECT_BACKGROUND_NODE = _box(-330.007, -448.8, 330.007, 448.8)   # scene box, FYI
ASPECT_BACKGROUND = BACKGROUND_FULL
ASPECT_ART        = _box(-105, -300.5, 105, -90.5)           # 210x210
ASPECT_TYPE       = _box(-119, -225, 121, -168)
ASPECT_NAME       = _box(-219, 164.5, 219, 221.5)
ASPECT_TEXT       = _box(-243, 236.5, 242, 350.5)

ASPECT_BACKGROUND_FILE = "aspect_background.png"

# Name and outline both take the aspect's own colour, so the outline reads as
# weight rather than as an edge -- the same trick the valence uses with black.
ASPECT_NAME_SIZE = 55
ASPECT_TYPE_SIZE = 30
ASPECT_TYPE_COLOR = (24, 24, 24)        # Color(0.0941176, ...)
ASPECT_TEXT_SIZE = 40
ASPECT_TEXT_COLOR = (255, 255, 255)

# Fallbacks for an aspect with no image_data, from aspect_card.gd's initialisers.
ASPECT_DEFAULT_PRIMARY = (244, 144, 144)
ASPECT_DEFAULT_SECONDARY = (237, 79, 95)


# --- Rite (event) ----------------------------------------------------------
RITE_BACKGROUND_NODE = _box(-330.007, -448.8, 330.007, 448.8)     # scene box, FYI
RITE_BACKGROUND = BACKGROUND_FULL
RITE_ART        = _box(-167, -187.5, 167, 187.5)             # 334x375
RITE_NAME       = _box(-219, 184.5, 219, 241.5)
RITE_TYPE       = _box(-116, 363, 124, 420)
RITE_TEXT       = _box(-249, 232, 249, 388)

RITE_NAME_SIZE = 55
RITE_NAME_COLOR = (4, 183, 255)              # Color(0.015686275, 0.7176471, 1)
RITE_NAME_OUTLINE_COLOR = (4, 108, 248)      # Color(0.015686275, 0.42352942, 0.972549)
RITE_TYPE_SIZE = 30
RITE_TYPE_COLOR = (24, 24, 24)
RITE_TEXT_SIZE = 40
RITE_TEXT_COLOR = (238, 84, 16)              # Color(0.93333334, 0.32941177, 0.0627451)

NAME_OUTLINE = GODOT_OUTLINE_3

# Which background a rite gets, from event_card.gd::set_event_visuals(). It
# branches on DISPLAY NAME, not on any field -- so this mapping is data, and it
# has to be kept in step with that match statement by hand.
RITE_BACKGROUND_BY_NAME = {
    "Smith": "upgrade",
    "Upgrade": "upgrade",
    "Trash": "trash",
    "Sever": "trash",
    "Rest": "rest",
    "Heal": "rest",
}
RITE_DEFAULT_BACKGROUND = "attribute"


def rite_variant(name) -> str:
    return RITE_BACKGROUND_BY_NAME.get(str(name or ""), RITE_DEFAULT_BACKGROUND)


def rite_background_file(name) -> str:
    """The baked background, correct only for a rite that overrides no colours."""
    return f"rite_background_{rite_variant(name)}.png"


def rite_mask_anim_file(name) -> str:
    """The ANIMATED mask sequence, or None if that variant has no animation.

    Only `attribute` is exported animated: all 21 rites in the live "Rites" deck
    resolve to it. The other three variants back boons (Boon_Left/Center/Right),
    which are a different mechanic, and stay static.
    """
    variant = rite_variant(name)
    return f"rite_background_{variant}_mask_anim.webp" if variant == "attribute" else None


def rite_mask_file(name) -> str:
    """The recolourable mask, for a rite carrying its own palette.

    reactant_card.gdshader composes a rite background as:

        col    = mix(primary_color, secondary_color, combinedPattern)
        output = mix(background_color, col, pattern * pattern)

    The mask is rendered with background=red, primary=blue, secondary=green, so
    each channel carries one term:

        R = 1 - pattern^2      G = pattern^2 * cp      B = pattern^2 * (1 - cp)

    giving pattern^2 = G + B and cp = G / (G + B).
    """
    return f"rite_background_{rite_variant(name)}_mask.png"


# `image_data` colours arrive as hex strings ("#1a0f4a") from the live rows, but
# EventVisuals.to_color also accepts 0-255 arrays, so both are handled.
def parse_color(value, fallback=None):
    if isinstance(value, str):
        text = value.strip().lstrip("#")
        if len(text) == 6:
            try:
                return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))
            except ValueError:
                return fallback
        return fallback
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return tuple(int(c) for c in value[:3])
        except (TypeError, ValueError):
            return fallback
    return fallback


def rite_text_color(rite: dict):
    """The colour a rite foregrounds its NAME and RULES TEXT in, or None.

    Port of EventVisuals.text_color_for(), applied by
    event_card.gd::set_event_text_color():

        text_color if the row authored one (for legibility), else primary_color,
        else None -- and None means keep the scene's own blue name and orange
        text rather than substituting anything.

    The name's OUTLINE takes the same colour, so it reads as weight rather than
    as an edge -- the same trick aspects use.
    """
    data = rite.get("image_data") or {}
    if not isinstance(data, dict):
        return None
    return parse_color(data.get("text_color")) or parse_color(data.get("primary_color"))


def rite_colors(rite: dict):
    """(background, primary, secondary) overrides, or None when the rite has none.

    21 of the 44 live rites carry their own palette; the rest keep the authored
    material colours and use the baked background instead.
    """
    data = rite.get("image_data") or {}
    if not isinstance(data, dict):
        return None
    bg = parse_color(data.get("background_color"))
    primary = parse_color(data.get("primary_color"))
    secondary = parse_color(data.get("secondary_color"))
    if bg is None and primary is None and secondary is None:
        return None
    return bg, primary, secondary


def aspect_colors(aspect: dict):
    """(primary, secondary) from an aspect's image_data.

    NOTE THE REVERSAL against the card convention, from
    GlobalVars.get_eigenfunction_colors() and aspect_card.gd:

      * The NAME LABEL takes `secondary_color`.
      * The ART's accent zone takes `primary_color` -- and its base zone takes
        `secondary_color`. So art and label are keyed off opposite fields.

    Getting these the wrong way round produces a card that looks plausible and is
    wrong, which is why it is pinned by a test.

    Live aspect rows store 0-255 ARRAYS while rite rows store HEX STRINGS, so
    this goes through the same `parse_color` the rites use rather than accepting
    arrays only. An aspect that ever picks up a hex value would otherwise fall
    silently back to the default pink -- a plausible-looking wrong card, which is
    the failure mode this whole function exists to avoid.
    """
    data = aspect.get("image_data")
    if not isinstance(data, dict):
        data = {}

    primary = parse_color(data.get("primary_color"), ASPECT_DEFAULT_PRIMARY)
    secondary = parse_color(data.get("secondary_color"), ASPECT_DEFAULT_SECONDARY)
    return primary, secondary
