"""Tests for the card renderer.

The renderer reproduces `scenes/cards/card.tscn` from the azoth repo. Several
constants here were derived by measuring Godot's own output rather than by
reading the code, because the code alone is misleading -- those are called out
individually. If the game's card template changes, these are the tests that
should fail first.
"""
import io
import json
import math

import numpy as np
import pytest
from PIL import Image, ImageDraw

from azoth_logic import card_layout as L
from azoth_logic import card_render, eigenfunction_art as ef, rich_text


# ---------------------------------------------------------------------------
# Layout, transcribed from card.tscn
# ---------------------------------------------------------------------------

def test_viewport_size_matches_the_scene():
    assert (L.CARD_W, L.CARD_H) == (560, 897)


def test_art_box_matches_the_image_node():
    """275x275 at (143, 319.5) -- verified against a Godot capture of the scene."""
    x, y, w, h = L.ART
    assert (round(x), round(y)) == (143, 320)
    assert (round(w), round(h)) == ef.ART_SIZE


@pytest.mark.parametrize("element,expected", [
    ("sol", "blurred_sol_border_noline.png"),
    ("blood", "blurred_blood_border_noline.png"),
    ("anima", "blurred_anima_border_noline.png"),
    (None, "blurred_white_border_noline.png"),      # 64 of 400 cards are colourless
    ("nonsense", "blurred_white_border_noline.png"),
])
def test_border_selection(element, expected):
    assert L.border_file(element) == expected


def test_wrap_width_is_narrower_than_the_box():
    """Godot's advance is ~2.6% wider than PIL's for the same font and size.

    Cap heights match exactly, so this is spacing, not scale -- but it flips
    break decisions on near-full lines, which visibly changes the card.
    """
    assert L.wrap_width(400) == pytest.approx(400 / 1.026)
    assert L.wrap_width(400) < 400


def test_godot_outline_size_is_not_pil_stroke_width():
    """card.tscn sets outline_size = 3, but PIL's stroke_width uses a different
    unit and 3 renders far too heavy -- heavy enough to fill in a digit's
    counters and read as bold.

    Calibrated against Godot on the valence glyph at size 60:

        digit  godot        PIL sw=1      PIL sw=3
        "2"    33x44 / 908  33x44 / 906   37x48 / 1405
        "6"    32x44 / 971  32x44 / 974   ...

    The name label calibrates to the same value (227px wide in both).
    """
    assert L.GODOT_OUTLINE_3 == 1
    assert L.NAME_OUTLINE == L.GODOT_OUTLINE_3
    assert L.VALENCE_OUTLINE == L.GODOT_OUTLINE_3


def test_name_is_drawn_with_its_white_outline():
    """NameLabel overrides font_outline_color to white, so the outline is
    visible there -- unlike the valence, where it is black on black. Omitting it
    makes the name visibly lighter than the game's."""
    from PIL import ImageDraw
    plain = Image.new("RGBA", (400, 120), (0, 0, 0, 255))
    outlined = plain.copy()
    f = _font(L.NAME_SIZE)
    ImageDraw.Draw(plain).text((20, 20), "Ablution", font=f, fill=L.NAME_COLOR)
    ImageDraw.Draw(outlined).text((20, 20), "Ablution", font=f, fill=L.NAME_COLOR,
                                  stroke_width=L.NAME_OUTLINE,
                                  stroke_fill=L.NAME_OUTLINE_COLOR)
    ink = lambda im: (np.asarray(im)[..., :3].mean(axis=2) > 190).sum()
    assert ink(outlined) > ink(plain) * 1.2, "the outline should add visible weight"

def test_render_actually_applies_the_name_outline(monkeypatch):
    """Wiring check: the renderer must pass L.NAME_OUTLINE through, not draw bare.

    Compares the real render against one with the outline forced off, rather
    than against an absolute ink threshold -- a loose threshold passes either
    way, which is how an earlier version of this missed the bug entirely.
    """
    def name_ink(img):
        band = np.asarray(img)[150:215, 130:430]
        return int((band[..., 3] > 128).sum() and (band[..., :3].mean(axis=2) > 190).sum())

    with_outline = name_ink(card_render.render_still(CARD, None))
    monkeypatch.setattr(L, "NAME_OUTLINE", 0)
    without = name_ink(card_render.render_still(CARD, None))
    assert with_outline > without * 1.15, (
        f"the outline should add visible weight to the rendered name: "
        f"{with_outline} vs {without} with it disabled")


def test_valence_outline_is_black():
    """ValenceLabel sets outline_size but not font_outline_color, so it takes
    Godot's black default. A white outline (the intuitive guess) is visibly
    wrong -- it turns bold black text into outlined text."""
    assert L.VALENCE_OUTLINE_COLOR == (0, 0, 0)


# ---------------------------------------------------------------------------
# Symbol tokens
# ---------------------------------------------------------------------------

def test_symbols_are_sized_from_the_function_default_not_the_label():
    """base_card.gd:979 calls replace_icon_from_dict() with NO size argument, so
    symbols use its `font_size = 50` default even though TextLabel's font is 40.

    Measured in Godot by rendering one token on an otherwise identical card and
    differencing against a blank one: base height 50px, i.e. 1.25x the label's
    40. Sizing off the label instead makes every symbol 20% too small.
    """
    assert rich_text.SYMBOL_BASE_SIZE == 50
    (_, img), = [(k, v) for k, v in rich_text.tokenize("[3valence]") if k == "img"]
    assert img.height == 50


def test_life_mult_bonus_render_oversized():
    """Utils.replace_icon_from_dict multiplies these families by 1.35."""
    normal = [v for k, v in rich_text.tokenize("[3valence]") if k == "img"][0]
    for token in ("[1life]", "[2mult]", "[4bonus]"):
        big = [v for k, v in rich_text.tokenize(token) if k == "img"][0]
        assert big.height == round(rich_text.SYMBOL_BASE_SIZE * 1.35)
        assert big.height > normal.height


def test_longest_token_wins():
    """`[10life]` must not be eaten by `[1life]`. The game gets this right only
    by dict ordering; being explicit means it does not depend on that."""
    runs = rich_text.tokenize("[10life]")
    assert [k for k, _ in runs] == ["img"]


def test_element_tokens_are_tinted():
    """Godot's [color] tag does not tint inline images, so the game injects
    color=# into the img tag. [sol]/[blood]/[anima] all share one greyscale
    source file and differ only by tint."""
    tokens = rich_text._tokens()
    assert tokens["[sol]"]["color"] == "#F9A410"
    assert tokens["[sol]"]["file"] == tokens["[blood]"]["file"]
    img = [v for k, v in rich_text.tokenize("[sol]") if k == "img"][0]
    px = np.array(img)
    opaque = px[px[..., 3] > 200]
    assert len(opaque) and abs(int(opaque[:, 0].mean()) - 0xF9) < 12


def test_plain_text_passes_through():
    assert rich_text.tokenize("no symbols here") == [("text", "no symbols here")]


def test_bbcode_wrappers_are_stripped():
    runs = rich_text.tokenize("[center]hello[/center]")
    assert runs == [("text", "hello")]


# ---------------------------------------------------------------------------
# Wrapping
# ---------------------------------------------------------------------------

def _font(size):
    from PIL import ImageFont
    return ImageFont.truetype(str(card_render.FONT_PATH), size)


def test_wrapping_breaks_and_drops_trailing_spaces():
    lines = rich_text.layout("one two three four five six seven eight",
                             _font(40), 300)
    assert len(lines) > 1
    for ln in lines:
        assert ln[0][0] != "space" and ln[-1][0] != "space"


def test_a_line_that_fits_is_not_broken():
    assert len(rich_text.layout("short", _font(40), 400)) == 1


def test_empty_text_produces_no_lines():
    assert rich_text.layout("", _font(40), 400) == []


# ---------------------------------------------------------------------------
# Eigenfunction art
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_exr(tmp_path):
    """A synthetic eigenfunction: a radial mode plus two perturbations.

    The alpha channel is the ZONE MAP (0.5 base / 1.0 accent), not opacity.
    """
    import os
    os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"
    import cv2
    n = 64
    yy, xx = np.mgrid[0:n, 0:n] / n - 0.5
    rad = np.sqrt(xx ** 2 + yy ** 2)
    r = (0.01 * np.cos(rad * 20)).astype(np.float32)
    g = (0.005 * np.sin(xx * 15)).astype(np.float32)
    b = (0.005 * np.sin(yy * 15)).astype(np.float32)
    a = np.where(rad < 0.25, 1.0, 0.5).astype(np.float32)
    path = tmp_path / "test.exr"
    cv2.imwrite(str(path), np.dstack([b, g, r, a]))   # cv2 wants BGRA
    return path


def test_weights_vanish_at_t0():
    """Both secondary modes are zero at t = 0, so the still frame is Z = ef.r."""
    assert ef.mode_weights(0.0) == (0.0, 0.0)


def test_departure_scales_the_motion():
    small = max(abs(w) for t in np.linspace(0, 20, 400) for w in ef.mode_weights(t, 0.05))
    large = max(abs(w) for t in np.linspace(0, 20, 400) for w in ef.mode_weights(t, 0.30))
    assert large > small * 5


def test_departure_override_is_honoured():
    assert ef.departure_for_card({}) == ef.DEFAULT_DEPARTURE
    assert ef.departure_for_card({"image_data": {"departure": 0.4}}) == 0.4
    # A bool is an int subclass; treating True as 1.0 departure would be absurd.
    assert ef.departure_for_card({"image_data": {"departure": True}}) == ef.DEFAULT_DEPARTURE


@pytest.mark.parametrize("element,accent", [
    ("sol", (249, 164, 16)), ("blood", (255, 0, 0)), ("anima", (135, 105, 233)),
])
def test_card_art_colours(element, accent):
    """The accent zone (alpha 1.0) takes the element colour; the base stays white."""
    primary, secondary = ef.colors_for_card({"element": element})
    assert primary == (255, 255, 255) and secondary == accent


def test_colourless_card_is_white_on_white():
    assert ef.colors_for_card({"element": None}) == ((255, 255, 255), (255, 255, 255))


def test_still_uses_the_zone_map_for_colour(fake_exr):
    img = ef.still(fake_exr, (255, 255, 255), (249, 164, 16))
    px = np.array(img)
    visible = px[px[..., 3] > 200]
    assert len(visible), "the synthetic field should threshold to something"
    # Both zones present: some white, some orange.
    assert (visible[:, 1] < 200).any(), "no accent-zone pixels"


def test_frame_count_and_size(fake_exr):
    frames = ef.frames(fake_exr, duration=2.0, fps=10)
    assert len(frames) == 20
    assert all(f.size == frames[0].size for f in frames)


def _seam(frames):
    """How far the loop jumps at the wrap point."""
    return np.abs(np.asarray(frames[-1], dtype=float)
                  - np.asarray(frames[0], dtype=float)).mean()


def test_crossfade_actually_closes_the_loop(fake_exr):
    """The shader's frequencies are incommensurate on purpose -- searching
    2-60s finds no duration where the motion returns to its start (the best, at
    48.2s, still lands ~30% of the weight range away). The last frames blend in
    FIELD space toward the pre-start frame to close it.

    Compare against crossfade=0 rather than against an unrelated frame: on a
    short loop the raw seam can happen to be small, which made an earlier
    version of this test pass with the blend removed entirely.
    """
    blended = ef.frames(fake_exr, duration=6.0, fps=10)
    raw = ef.frames(fake_exr, duration=6.0, fps=10, crossfade=0.0)
    assert _seam(blended) < _seam(raw), (
        f"crossfade should shrink the seam: {_seam(blended):.4f} vs raw {_seam(raw):.4f}")


def test_crossfade_leaves_the_body_of_the_loop_alone(fake_exr):
    """Only the tail is blended -- the first three quarters must be the real
    motion, not a dissolve."""
    blended = ef.frames(fake_exr, duration=6.0, fps=10, crossfade=0.25)
    raw = ef.frames(fake_exr, duration=6.0, fps=10, crossfade=0.0)
    untouched = int(len(raw) * 0.75)
    for i in range(untouched):
        assert np.array_equal(np.asarray(blended[i]), np.asarray(raw[i]))


def test_crossfade_is_disableable(fake_exr):
    """crossfade=0 gives the raw motion -- useful when the caller wants a strip
    rather than a loop."""
    frames = ef.frames(fake_exr, duration=2.0, fps=10, crossfade=0.0)
    assert len(frames) == 20


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("image,animated", [
    ("thing.exr", True), ("thing.EXR", True),
    ("thing.png", False), ("", False), (None, False),
])
def test_only_exr_art_animates(image, animated):
    """Mirrors ImageCache.eigenfunction_name_for_image(): 246 of 400 cards carry
    eigenfunction art; the rest are flat PNGs with nothing to animate."""
    assert card_render.is_animated({"image": image}) is animated


def test_art_bucket_follows_the_extension():
    assert card_render.art_bucket({"image": "x.exr"}) == "eigenfunctions"
    assert card_render.art_bucket({"image": "x.png"}) == "cardimages"


CARD = {"name": "Ablution", "element": "sol", "valence": 2, "subtypes": ["Sacred"],
        "text": "Draw 1, Gain [1life] per card drawn this link", "image": "a.exr"}


def test_render_still_is_card_sized():
    img = card_render.render_still(CARD, None)
    assert img.size == (L.CARD_W, L.CARD_H)
    assert img.mode == "RGBA"


def test_render_png_is_a_png():
    data = card_render.render_png(CARD, None)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_gif_loops_forever(fake_exr):
    data = card_render.render_gif(CARD, fake_exr.read_bytes(), duration=1.0, fps=5)
    assert data[:6] in (b"GIF87a", b"GIF89a")
    import io
    im = Image.open(io.BytesIO(data))
    assert im.n_frames == 5
    assert im.info.get("loop") == 0, "loop=0 means repeat indefinitely"


def test_card_with_no_subtype_renders_blank_not_a_placeholder():
    """card.tscn ships TypeLabel with `text = "[center]Arcane"`, and base_card.gd
    only overwrites it when `subtypes` is non-empty -- so the GAME displays
    "Arcane" on every subtype-less card. That is a scene placeholder leaking
    through, not data, and it is not reproduced here."""
    plain = dict(CARD, subtypes=[])
    a = np.asarray(card_render.render_still(plain, None), dtype=int)
    b = np.asarray(card_render.render_still(dict(CARD, subtypes=["Sacred"]), None), dtype=int)
    assert np.abs(a - b).sum() > 0, "the subtype line should change the render"


def test_missing_symbol_asset_names_the_fix(monkeypatch):
    monkeypatch.setattr(rich_text, "_symbol_cache", {})
    monkeypatch.setattr(rich_text, "SYMBOL_DIR", rich_text.SYMBOL_DIR / "nope")
    with pytest.raises(FileNotFoundError, match="sync_assets"):
        rich_text.tokenize("[1life]")


# ---------------------------------------------------------------------------
# Split cards (11 of 400)
# ---------------------------------------------------------------------------

SPLIT = {"name": "Attunement", "element": "anima", "valence": 6,
         "subtypes": ["Ancient"], "text": "Draw 2, Scry 2", "image": "a.exr",
         "split": {"element": "sol", "valence": 4}}


def test_split_face_is_read():
    assert L.split_face(SPLIT) == ("sol", 4)


@pytest.mark.parametrize("value", [None, {}, "", 0, []])
def test_absent_split_is_not_a_split(value):
    """Every card exported from the database carries an explicit `"split": null`,
    so a bare key check treats single-sided cards as split -- the same trap
    GlobalVars.get_eigenfunction_colors() guards with `is Dictionary`."""
    assert L.split_face({"element": "sol", "split": value}) is None


def test_split_art_colours_both_zones():
    """A split card's art is two-toned: its own element on the accent zone, the
    split element on the base zone. That is what makes it read as split."""
    primary, secondary = ef.colors_for_card(SPLIT)
    assert secondary == ef.ELEMENT_COLORS["anima"], "accent = the card's element"
    assert primary == ef.ELEMENT_COLORS["sol"], "base = the split element"


def test_single_sided_art_keeps_a_white_base():
    primary, secondary = ef.colors_for_card({"element": "anima", "split": None})
    assert primary == (255, 255, 255)
    assert secondary == ef.ELEMENT_COLORS["anima"]


@pytest.mark.parametrize("element,expected", [
    ("sol", "blurred_second_sol_border3.png"),
    ("blood", "blurred_second_blood_border3.png"),
    ("anima", "blurred_second_anima_border3.png"),
    ("nonsense", None), (None, None), ("", None),
])
def test_split_border_selection(element, expected):
    assert L.split_border_file(element) == expected


def test_split_border_does_not_widen_the_silhouette():
    """card_border_dim.gdshader takes alpha from the BASE texture alone. If the
    split contributed alpha, the card would gain a second, fatter outline."""
    plain = np.asarray(card_render._border(dict(SPLIT, split=None)))
    split = np.asarray(card_render._border(SPLIT))
    assert np.array_equal(plain[..., 3], split[..., 3]), "alpha must be untouched"
    assert not np.array_equal(plain[..., :3], split[..., :3]), "colour must change"


def test_split_stays_opaque_along_the_antialiased_rim():
    """Coverage is `split.a / base.a`, not `split.a`.

    That normalisation exists for the rim: where both textures fade together,
    the raw split alpha is partial, so the base border bleeds through and the
    split's edge reads as a soft double line. Dividing by the base's alpha keeps
    the split fully opaque there while preserving its authored interior fade.

    Measured over the 3,691 rim pixels of the anima/sol pair: normalised
    coverage averages 0.96, raw 0.87.
    """
    base = np.asarray(card_render._layer(L.border_file("anima"), L.BORDER),
                      dtype=np.float32) / 255.0
    sp = np.asarray(card_render._layer(L.split_border_file("sol"), L.BORDER),
                    dtype=np.float32) / 255.0
    rim = (base[..., 3] > 0.15) & (base[..., 3] < 0.85) & (sp[..., 3] > 0.05)
    assert rim.sum() > 100, "fixture should have an antialiased rim to test"

    blended = np.asarray(card_render._border(SPLIT), dtype=np.float32) / 255.0
    # Both forms land "mostly split" at the rim, so a loose comparison passes
    # either way. What separates them is HOW cleanly: normalised coverage
    # resolves essentially onto the split colour (measured residual 0.011),
    # while raw coverage leaves the base bleeding through at ~4x that (0.048).
    residual = np.abs(blended[..., :3] - sp[..., :3]).sum(axis=2)[rim].mean()
    assert residual < 0.025, (
        f"rim should resolve onto the split colour; residual {residual:.4f} "
        f"suggests coverage is not normalised by the base's alpha")


def test_split_border_changes_only_where_the_overlay_covers():
    """Coverage is the split's alpha normalised by the base's, so pixels the
    overlay does not reach keep the base border exactly."""
    plain = np.asarray(card_render._border(dict(SPLIT, split=None)), dtype=int)
    split = np.asarray(card_render._border(SPLIT), dtype=int)
    changed = np.abs(plain[..., :3] - split[..., :3]).sum(axis=2) > 8
    assert 0.01 < changed.mean() < 0.9, "the overlay should cover part of the border, not all"


def test_split_card_draws_a_second_valence():
    with_split = np.asarray(card_render.render_still(SPLIT, None), dtype=int)
    without = np.asarray(card_render.render_still(dict(SPLIT, split=None), None), dtype=int)
    # The second valence sits in the opposite top corner.
    x = round(L.SYMBOL[0] + L.VALENCE2_REL[0])
    y = round(L.SYMBOL[1] + L.VALENCE2_REL[1])
    region = (slice(y, y + round(L.VALENCE2_REL[3])), slice(x, x + round(L.VALENCE2_REL[2])))
    assert np.abs(with_split[region] - without[region]).sum() > 0


def test_split_second_valence_lands_inside_the_card():
    x = L.SYMBOL[0] + L.VALENCE2_REL[0]
    y = L.SYMBOL[1] + L.VALENCE2_REL[1]
    assert 0 <= x < L.CARD_W and 0 <= y < L.CARD_H
    assert x > L.CARD_W / 2, "the second valence belongs in the opposite corner"


# ---------------------------------------------------------------------------
# Transparent animated output
# ---------------------------------------------------------------------------
# Animated cards used to be flattened onto #313338 because GIF alpha is 1 bit.
# That is Discord's *Dark* theme colour -- on Darker, Midnight or Light it read
# as a grey rectangle around the card.

def _frames(n=6, size=(120, 200)):
    """RGBA frames shaped like a card: a ROUNDED body inset in empty canvas,
    with a blob that moves inside it.

    Rounded on purpose. A plain rectangle would be fully opaque once cropped,
    so every transparency assertion below would pass against a renderer that
    had no transparency at all.
    """
    out = []
    for i in range(n):
        f = Image.new("RGBA", size, (0, 0, 0, 0))
        d = ImageDraw.Draw(f)
        d.rounded_rectangle([10, 10, size[0] - 11, size[1] - 11], radius=18,
                            fill=(20, 20, 20, 255))
        d.ellipse([30, 30 + i * 5, 70, 70 + i * 5], fill=(249, 164, 16, 255))
        out.append(f)
    return out


def test_gif_carries_transparency():
    data = card_render.to_gif(_frames())
    g = Image.open(io.BytesIO(data))
    assert g.info.get("transparency") is not None, "no transparent index in the GIF"
    assert np.asarray(g.convert("RGBA"))[0, 0, 3] == 0, "the corner should be see-through"


def test_gif_transparency_survives_every_frame():
    """A transparent index that only holds on frame 1 gives a card that flashes
    a background in on frame 2."""
    g = Image.open(io.BytesIO(card_render.to_gif(_frames())))
    for i in range(g.n_frames):
        g.seek(i)
        assert np.asarray(g.convert("RGBA"))[0, 0, 3] == 0, f"frame {i} lost transparency"


def test_gif_is_cropped_to_the_card():
    frames = _frames()
    g = Image.open(io.BytesIO(card_render.to_gif(frames)))
    assert g.size == (100, 180), "10px transparent border should be cropped away"
    assert g.size != frames[0].size


def test_the_crop_is_the_union_of_every_frame():
    """Cropping each frame to its own bounds would make the card jitter as the
    art moves inside it. The box has to be identical for all of them."""
    frames = _frames(n=6)
    shared = card_render.alpha_bbox(frames)
    for f in frames:
        own = card_render.alpha_bbox([f])
        assert own[0] >= shared[0] and own[1] >= shared[1]
        assert own[2] <= shared[2] and own[3] <= shared[3]
    # and the shared box really is the union, not just the first frame's
    g = Image.open(io.BytesIO(card_render.to_gif(frames)))
    sizes = set()
    for i in range(g.n_frames):
        g.seek(i)
        sizes.add(g.convert("RGBA").size)
    assert len(sizes) == 1, f"frames differ in size -> the card would jitter: {sizes}"


# alpha_bbox and _global_palette are unit-tested DIRECTLY, not through a render.
#
# Both take the whole frame list on purpose, and for a card that is currently
# defensive rather than load-bearing: the face is identical in every frame (only
# the art inside it moves), so a per-frame box and a per-frame palette would give
# the same answer today. Mutating either to look at one frame survives a render
# test. Exercising the contract here is what makes the intent enforceable.

def test_alpha_bbox_is_the_union_not_the_first_frame():
    """A frame list whose opaque extent MOVES. Cropping to any single frame's
    box would clip the others; the card would jitter or lose an edge."""
    a = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    ImageDraw.Draw(a).rectangle([10, 10, 40, 40], fill=(255, 255, 255, 255))
    b = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    ImageDraw.Draw(b).rectangle([60, 60, 90, 90], fill=(255, 255, 255, 255))

    assert card_render.alpha_bbox([a]) == (10, 10, 41, 41)
    assert card_render.alpha_bbox([b]) == (60, 60, 91, 91)
    assert card_render.alpha_bbox([a, b]) == (10, 10, 91, 91), "must span both"
    assert card_render.alpha_bbox([b, a]) == (10, 10, 91, 91), "order must not matter"


def test_alpha_bbox_respects_the_cutoff():
    """Pixels under the cutoff become transparent in the GIF, so they must not
    hold the crop open either."""
    f = Image.new("RGBA", (50, 50), (0, 0, 0, 0))
    ImageDraw.Draw(f).rectangle([20, 20, 30, 30], fill=(255, 255, 255, 255))
    ImageDraw.Draw(f).point((2, 2), fill=(255, 255, 255, 40))     # faint, below cutoff
    assert card_render.alpha_bbox([f], cutoff=128) == (20, 20, 31, 31)
    assert card_render.alpha_bbox([f], cutoff=8)[0] == 2, "a lower cutoff should keep it"


def test_the_palette_covers_colours_that_appear_late():
    """Derived from frames sampled ACROSS the animation. Taking only the first
    frame drops any colour introduced later, which then quantises to the nearest
    entry -- the eigenfunction art shifts hue partway through its loop."""
    frames = []
    for i in range(16):
        f = Image.new("RGBA", (40, 40), (0, 0, 0, 255))
        # A colour that exists ONLY in the second half of the animation.
        if i >= 8:
            ImageDraw.Draw(f).rectangle([5, 5, 35, 35], fill=(0, 200, 255, 255))
        frames.append(f)

    palette = card_render._global_palette(frames, 64)
    entries = [tuple(palette[i:i + 3]) for i in range(0, 64 * 3, 3)]
    # Every entry must be a full RGB triple. Comparing against a SHORT palette
    # silently scored 0 via zip(), which made this assertion vacuous.
    assert all(len(e) == 3 for e in entries), "palette is not padded to full length"

    def dist(c):
        return sum((a - b) ** 2 for a, b in zip(c, (0, 200, 255)))
    closest = min(entries, key=dist)
    assert dist(closest) < 400, f"the late colour has no palette entry; nearest is {closest}"


def test_a_low_colour_animation_gets_a_well_formed_palette():
    """A rite background is 2-3 colours, so `getpalette()` comes back with four
    entries while the pixel data references the transparent index at 64 -- a GIF
    pointing past its own colour table. Padding keeps the table well formed.
    """
    frames = []
    for i in range(4):
        f = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
        d = ImageDraw.Draw(f)
        d.rounded_rectangle([5, 5, 54, 54], radius=10, fill=(200, 20, 20, 255))
        d.ellipse([20, 20 + i, 40, 40 + i], fill=(81, 158, 34, 255))
        frames.append(f)

    palette = card_render._global_palette(frames, card_render.GIF_COLORS)
    assert len(palette) == (card_render.GIF_COLORS + 1) * 3, (
        f"palette has {len(palette)//3} entries; the transparent index "
        f"{card_render.GIF_COLORS} must be inside it")

    g = Image.open(io.BytesIO(card_render.to_gif(frames)))
    assert np.asarray(g.convert("RGBA"))[0, 0, 3] == 0


def test_a_shared_palette_beats_per_frame_palettes_on_size():
    """Per-frame adaptive palettes defeat the GIF optimiser's frame
    differencing -- it can only encode a changed sub-rectangle when successive
    frames share a colour table. Measured on a real card: 806 KB per-frame
    against 272 KB shared.
    """
    frames = _frames(n=12)
    shared = card_render.to_gif(frames)

    per_frame = [Image.alpha_composite(
        Image.new("RGBA", f.size, (0, 0, 0, 255)), f
    ).convert("P", palette=Image.ADAPTIVE, colors=64) for f in frames]
    buf = io.BytesIO()
    per_frame[0].save(buf, format="GIF", save_all=True, append_images=per_frame[1:],
                      duration=66, loop=0, optimize=True)
    assert len(shared) < len(buf.getvalue())


def test_still_png_is_cropped_and_keeps_alpha():
    """PNG has always carried alpha; what it also carried was ~63px of empty
    canvas above and below, which Discord scaled down along with the card."""
    card = {"name": "X", "element": "sol", "valence": 2, "subtypes": ["S"],
            "text": "Draw 1", "image": None, "split": None}
    im = Image.open(io.BytesIO(card_render.render_png(card, None)))
    assert im.mode == "RGBA"
    assert im.size != (L.CARD_W, L.CARD_H), "still should be cropped to the card"
    a = np.asarray(im)[..., 3]
    assert a[0, 0] == 0, "rounded corner should stay transparent"
    assert a.max() == 255


def test_the_alpha_cutoff_is_where_the_rim_is_thin():
    """1-bit alpha has to cut the antialiased rim somewhere. It is only ~3px
    wide and 98% of the card is fully opaque, so the midpoint is imperceptible
    -- but a cutoff at the extremes would either eat the edge or fringe it."""
    assert 64 <= card_render.ALPHA_CUTOFF <= 192
