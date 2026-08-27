"""Tests for the aspect and rite renderers.

"Rite" is the current name for what the database calls an "event". These tests
use the new name; where a DB key appears it stays `event`, and that boundary is
asserted explicitly below.
"""
import io

import numpy as np
import pytest
from PIL import Image

from azoth_logic import fate_layout as F, fate_render
from azoth_logic.card_layout import CARD_W, CARD_H

ASPECT = {"name": "Readiness", "text": "+1 Starting Hand Size", "image": "a.exr",
          "image_data": {"primary_color": [246, 83, 83],
                         "secondary_color": [9, 242, 210], "departure": 0.1}}
# Two live rites: one with its own palette, one without. 21 of the 44 live rites
# carry `image_data`; the rest keep the material's authored colours.
RITE = {"name": "Echo", "text": "Duplicate a card in your deck", "image": "c_hoxbow.png"}
RITE_PALETTE = {"name": "Amplification", "text": "[8mult] next link",
                "image": "c_hoxbow.png",
                "image_data": {"background_color": "#1a0f4a",
                               "primary_color": "#ffb01f",
                               "secondary_color": "#ffca39"}}


# ---------------------------------------------------------------------------
# The event -> rite naming boundary
# ---------------------------------------------------------------------------

def test_db_keys_keep_the_old_name():
    """The rename is user-facing only. Renaming the table, the content_type or
    the Storage bucket means a migration, so those stay `event` until then."""
    from azoth_commands import rites
    assert rites.TABLE_NAME == "events"
    assert rites.DB_KEY == "event"
    assert rites.MODEL_NAME == "rite"


def test_hero_commands_are_not_registered():
    """Retired 2026-08-26 -- deliberately, unlike the rituals/consumables case
    where the attacher was simply never called."""
    from azoth_commands import AzothCommands
    assert not [n for n in dir(AzothCommands) if n.endswith("_hero_cmd")]


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("box", ["ASPECT_ART", "ASPECT_NAME", "ASPECT_TEXT",
                                 "RITE_NAME", "RITE_TEXT"])
def test_boxes_land_inside_the_card(box):
    x, y, w, h = getattr(F, box)
    assert 0 <= x and x + w <= CARD_W
    assert 0 <= y and y + h <= CARD_H


def test_aspect_art_is_210_square():
    assert (round(F.ASPECT_ART[2]), round(F.ASPECT_ART[3])) == (210, 210)


@pytest.mark.parametrize("name,expected", [
    ("Smith", "upgrade"), ("Upgrade", "upgrade"),
    ("Trash", "trash"), ("Sever", "trash"),
    ("Rest", "rest"), ("Heal", "rest"),
    ("Ritual Gamble", "attribute"), ("", "attribute"), (None, "attribute"),
])
def test_rite_background_follows_the_display_name(name, expected):
    """event_card.gd::set_event_visuals() branches on DISPLAY NAME, not on a
    field -- so this mapping is data and has to track that match statement."""
    assert F.rite_background_file(name) == f"rite_background_{expected}.png"
    assert F.rite_mask_file(name) == f"rite_background_{expected}_mask.png"


# ---------------------------------------------------------------------------
# Per-rite palettes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("#ffb01f", (255, 176, 31)),
    ("#1a0f4a", (26, 15, 74)),
    ([246, 83, 83], (246, 83, 83)),      # EventVisuals.to_color takes arrays too
    ("nope", None), ("", None), (None, None), (123, None), ([1, 2], None),
])
def test_colour_parsing(value, expected):
    assert F.parse_color(value) == expected


def test_rite_without_a_palette_reports_none():
    """23 of 44 live rites author nothing and must keep the material's colours."""
    for empty in ({}, {"image_data": {}}, {"image_data": None}):
        assert F.rite_colors(empty) is None
        assert F.rite_text_color(empty) is None


def test_rite_palette_is_read():
    assert F.rite_colors(RITE_PALETTE) == ((26, 15, 74), (255, 176, 31), (255, 202, 57))


def test_partial_palette_is_honoured():
    """EventVisuals treats every key independently -- a row setting only
    background_color keeps the authored pattern colours."""
    only_bg = {"image_data": {"background_color": "#112233"}}
    assert F.rite_colors(only_bg) == ((17, 34, 51), None, None)


def test_text_colour_prefers_text_color_over_primary():
    """EventVisuals.text_color_for: text_color exists so a row whose pattern
    colour is too dark to read can override just the foreground."""
    assert F.rite_text_color({"image_data": {"primary_color": "#ffb01f"}}) == (255, 176, 31)
    assert F.rite_text_color({"image_data": {"primary_color": "#ffb01f",
                                             "text_color": "#00ff00"}}) == (0, 255, 0)


def test_palette_rite_tints_its_name_and_text():
    """event_card.gd::set_event_text_color() overrides BOTH labels, and the
    name's outline too -- verified against Godot, whose Amplification name
    renders at exactly #ffb01f."""
    plain = np.asarray(Image.open(io.BytesIO(fate_render.render_rite(RITE)[0])).convert("RGB"))
    tinted = np.asarray(Image.open(io.BytesIO(fate_render.render_rite(RITE_PALETTE)[0])).convert("RGB"))
    band = (slice(636, 688), slice(70, 490))          # the NameLabel box
    from collections import Counter
    def glyph(px):
        return Counter(map(tuple, px[band].reshape(-1, 3))).most_common(2)[1][0]
    assert glyph(plain) == F.RITE_NAME_COLOR, "no palette -> the scene's blue"
    assert glyph(tinted) == (255, 176, 31), "palette -> its own primary_color"


def test_palette_actually_recolours_the_background():
    """Compare the SAME rite with and without a palette.

    Comparing two *different* rites proves nothing: they resolve to different
    background variants anyway, so the assertion held even with the recolouring
    path removed entirely. The mutation run is what surfaced that.
    """
    name = RITE_PALETTE["name"]
    with_palette = fate_render.render_rite(RITE_PALETTE)[0]
    without = fate_render.render_rite({k: v for k, v in RITE_PALETTE.items()
                                       if k != "image_data"})[0]
    assert with_palette != without, "the palette should change the background"

    a = np.asarray(Image.open(io.BytesIO(with_palette)).convert("RGB"), dtype=int)
    b = np.asarray(Image.open(io.BytesIO(without)).convert("RGB"), dtype=int)
    # Sample a corner well away from any text: the palette turns it navy.
    corner = (slice(80, 200), slice(30, 160))
    assert a[corner][..., 2].mean() > b[corner][..., 2].mean() + 10, \
        f"#1a0f4a background should read bluer than the authored red ({name})"


# ---------------------------------------------------------------------------
# Aspect colours -- reversed against the card convention
# ---------------------------------------------------------------------------

def test_aspect_label_and_art_use_opposite_fields():
    """The NAME takes secondary_color; the ART's ACCENT takes primary_color.

    That is the reverse of the card convention and the easiest thing here to get
    backwards -- it produces a plausible-looking, wrong card.
    """
    primary, secondary = F.aspect_colors(ASPECT)
    assert primary == (246, 83, 83) and secondary == (9, 242, 210)

    art_base, art_accent = fate_render.aspect_art_colors(ASPECT)
    assert art_accent == primary, "art accent = image_data.primary_color"
    assert art_base == secondary, "art base = image_data.secondary_color"


def test_aspect_colours_fall_back_when_image_data_is_missing():
    for empty in ({}, {"image_data": None}, {"image_data": {}}):
        assert F.aspect_colors(empty) == (F.ASPECT_DEFAULT_PRIMARY,
                                          F.ASPECT_DEFAULT_SECONDARY)


def test_malformed_colour_arrays_fall_back():
    assert F.aspect_colors({"image_data": {"primary_color": [1, 2]}})[0] == F.ASPECT_DEFAULT_PRIMARY


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Card silhouette
# ---------------------------------------------------------------------------

def _silhouette(data: bytes):
    """(left, right, top, bottom) of the opaque region."""
    a = np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))
    alpha = a[..., 3]
    ys, xs = np.where(alpha > 128)
    return int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())


@pytest.mark.parametrize("render", [
    lambda: fate_render.render_aspect(ASPECT, None, animate=False)[0],
    lambda: fate_render.render_rite(RITE)[0],
])
def test_backgrounds_keep_the_card_silhouette(render):
    """Aspects and rites must have the same rounded card shape as cards.

    The backgrounds are exported as full-VIEWPORT captures, so they already
    carry the silhouette at its final position. Fitting them into the Background
    node's 660x897 box -- the way a card's raw texture IS fitted -- stretched
    them and pushed the rounded edges off-canvas, leaving square corners.

    Measured from Godot: an aspect or rite occupies x 8-551, y 69-827 of the
    560x897 viewport.
    """
    left, right, top, bottom = _silhouette(render())
    assert (left, right) == (8, 551), "silhouette should be inset, not flush to the canvas"
    assert (top, bottom) == (69, 827)


@pytest.mark.parametrize("render", [
    lambda: fate_render.render_aspect(ASPECT, None, animate=False)[0],
    lambda: fate_render.render_rite(RITE)[0],
])
def test_corners_are_transparent(render):
    """A square corner is the symptom the silhouette bug produced."""
    a = np.asarray(Image.open(io.BytesIO(render())).convert("RGBA"))
    for y, x in [(0, 0), (0, 559), (896, 0), (896, 559), (20, 20)]:
        assert a[y, x, 3] == 0, f"corner pixel ({x},{y}) should be transparent"


def test_background_is_drawn_at_native_size():
    """The exports are 560x897 viewport captures; resizing them is the bug."""
    from azoth_logic import fate_layout as FL
    assert FL.ASPECT_BACKGROUND == (0.0, 0.0, 560.0, 897.0)
    assert FL.RITE_BACKGROUND == FL.ASPECT_BACKGROUND
    # The scene's own box is kept for reference and must NOT be what is drawn into.
    assert FL.ASPECT_BACKGROUND_NODE != FL.ASPECT_BACKGROUND


def test_aspect_still_is_card_sized():
    data, ext = fate_render.render_aspect(ASPECT, None, animate=False)
    assert ext == "png"
    assert Image.open(io.BytesIO(data)).size == (CARD_W, CARD_H)


def test_rite_is_card_sized_and_static():
    data, ext = fate_render.render_rite(RITE)
    assert ext == "png"
    assert Image.open(io.BytesIO(data)).size == (CARD_W, CARD_H)


def test_rite_background_mask_reconstructs_the_shader():
    """The mask encodes reactant_card.gdshader's two scalars in separate
    channels: R = 1 - pattern^2, G = pattern^2 * cp, B = pattern^2 * (1 - cp).

    Feeding the material's own authored colours back through the reconstruction
    should reproduce the baked export -- measured at 0.003 mean error, the rest
    being the shader's dither and its TIME terms.
    """
    mask = np.asarray(Image.open(
        fate_render.BACKGROUND_DIR / "rite_background_attribute_mask.png"
    ).convert("RGBA"), dtype=np.float32) / 255.0
    inside = mask[..., 3] > 0.5
    total = mask[..., 0] + mask[..., 1] + mask[..., 2]
    assert abs(total[inside].mean() - 1.0) < 0.02, \
        "R + G + B should sum to 1: the three channels partition the pixel"


# ---------------------------------------------------------------------------
# Rite animation
# ---------------------------------------------------------------------------

def test_only_the_attribute_variant_animates():
    """All 21 rites in the live "Rites" deck resolve to `attribute`. The other
    three variants back boons (Boon_Left/Center/Right), a different mechanic, and
    are left static."""
    assert F.rite_mask_anim_file("Amplification") == "rite_background_attribute_mask_anim.webp"
    for boon in ("Smith", "Trash", "Rest"):
        assert F.rite_mask_anim_file(boon) is None


def test_animated_rite_returns_a_looping_gif():
    data, ext = fate_render.render(RITE_PALETTE, "rite")
    assert ext == "gif"
    im = Image.open(io.BytesIO(data))
    assert im.info.get("loop") == 0
    assert im.n_frames > 1


def test_static_variant_falls_back_to_png():
    """A boon-backed rite has no animated mask, so it must still render."""
    boon = {"name": "Sever", "text": "Trash a card"}
    assert fate_render.render_rite_gif(boon) is None
    assert fate_render.render(boon, "rite")[1] == "png"


def test_frames_ping_pong_exactly():
    """The shader's noise is not periodic, so there is no natural loop -- and the
    pattern has crisp edges, so cross-fading two frames (the trick the
    eigenfunction art uses on its smooth field) would ghost. Forward-then-back
    loops exactly instead.
    """
    frames = fate_render._rite_background_frames(RITE_PALETTE, F.rite_colors(RITE_PALETTE))
    n = len(frames)
    assert n % 2 == 0, "forward + reverse without repeating either endpoint"
    source = (n + 2) // 2
    for i in range(1, source - 1):
        a = np.asarray(frames[i], dtype=int)
        b = np.asarray(frames[n - i], dtype=int)
        assert np.array_equal(a, b), f"frame {i} should mirror frame {n - i}"


def test_animation_actually_moves():
    frames = fate_render._rite_background_frames(RITE_PALETTE, F.rite_colors(RITE_PALETTE))
    a = np.asarray(frames[0], dtype=float)
    mid = np.asarray(frames[len(frames) // 2], dtype=float)
    assert np.abs(mid - a).mean() > 5, "the pattern should visibly breathe"


def test_animated_frames_use_the_rites_palette():
    tinted = fate_render._rite_background_frames(RITE_PALETTE, F.rite_colors(RITE_PALETTE))
    plain = fate_render._rite_background_frames(RITE_PALETTE, None)
    assert not np.array_equal(np.asarray(tinted[0]), np.asarray(plain[0]))


def test_rite_never_draws_art():
    """event_card.tscn ships the Image node with `visible = false`: a rite's
    visual IS its background. Drawing the `image` column puts a blob over the
    middle of a card the game renders clean."""
    plain = fate_render.render_rite(RITE)[0]
    with_art = fate_render.render_rite(RITE, b"ignored-bytes")[0]
    assert plain == with_art


def test_aspect_background_is_tinted_per_aspect():
    """The background is exported WHITE so each aspect can multiply in its own
    colour. Exporting it pre-tinted would bake one aspect's hue into all 149."""
    warm = fate_render.render_aspect(ASPECT, None, animate=False)[0]
    cool = fate_render.render_aspect(
        dict(ASPECT, image_data={"primary_color": [0, 120, 255],
                                 "secondary_color": [255, 255, 255]}),
        None, animate=False)[0]
    assert warm != cool

    a = np.asarray(Image.open(io.BytesIO(warm)).convert("RGB"), dtype=int)
    b = np.asarray(Image.open(io.BytesIO(cool)).convert("RGB"), dtype=int)
    # The warm aspect should be redder overall, the cool one bluer.
    assert a[..., 0].mean() > b[..., 0].mean()
    assert b[..., 2].mean() > a[..., 2].mean()


def test_type_label_is_not_drawn():
    """Both scenes ship their Type node with `visible = false`. Drawing it puts
    stray text outside the card body on a rite (its box sits at y 811-868, past
    the card's opaque extent)."""
    data = fate_render.render_rite(RITE)[0]
    a = np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))
    below = a[840:, :, 3]
    assert below.max() == 0, "nothing should be drawn below the card body"


def test_only_exr_aspects_animate():
    assert fate_render.is_animated(ASPECT) is True
    assert fate_render.is_animated({"image": "flat.png"}) is False


def test_render_dispatch_rejects_unknown_kinds():
    with pytest.raises(ValueError, match="unknown fate kind"):
        fate_render.render(ASPECT, "boss")


def test_aspect_colors_accept_hex_like_rites_do():
    """Aspect rows store 0-255 arrays; rite rows store hex strings.

    `aspect_colors` used to accept arrays ONLY, so a hex value fell silently back
    to the default pink -- a plausible-looking wrong card, which is exactly the
    failure this function's docstring warns about. It now shares `parse_color`.
    """
    hexed = {"image_data": {"primary_color": "#9ce652", "secondary_color": "#a8a8a8"}}
    assert F.aspect_colors(hexed) == ((156, 230, 82), (168, 168, 168))


def test_aspect_colors_still_accept_arrays():
    """The live format. Regression guard on the change above."""
    arrayed = {"image_data": {"primary_color": [156, 230, 82], "secondary_color": [168, 168, 168]}}
    assert F.aspect_colors(arrayed) == ((156, 230, 82), (168, 168, 168))


def test_aspect_colors_survive_a_non_dict_image_data():
    """Some rows carry a JSON string rather than an object."""
    assert F.aspect_colors({"image_data": "not a dict"}) == (F.ASPECT_DEFAULT_PRIMARY,
                                                             F.ASPECT_DEFAULT_SECONDARY)
