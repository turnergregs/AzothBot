"""Tests for a rite's procedural background (`image_data.background`).

The incident: on 2026-09-25 every live rite's art was moved to a procedural
background made in the game's rite look editor, and `/render` kept drawing the
old baked pattern for all 22 of them, because nothing here read the new key.

The field itself is procedural_art.Field, pinned against the GPU in
test_procedural_art.py; what is tested here is what is rite-specific: which
rites take the path, the frame mapping, the palette and the loop.
"""
import io

import numpy as np
import pytest
from PIL import Image

from azoth_logic import art_cache, fate_render, rite_background as RB
from azoth_logic.card_layout import CARD_W, CARD_H

# Amplification as it shipped in the 2026-09-25 bulk_update.
BACKGROUND = {
    "version": 1, "family": "triangle", "frame": "full",
    "modes": [[1, 3], [3, 5], [1, 3], [2, 4]],
    "params": [[1, 0], [0.2, 0], [0.8, 0], [1, 0]],
    "is_even": [1, 1, 1, 1], "is_dirichlet": [1, 1, 1, 1],
    "gap": 0.46, "scale": 0.98, "center": [0, -1.6288], "rotation": 0,
    "framing": "whole", "softness": 0.15, "roundness": 0.25, "silhouette": "circle",
    "fill": {"base": "off"},
}
PALETTE = {"background_color": "#1a0f4a", "primary_color": "#ffb01f",
           "secondary_color": "#ffca39"}
RITE = {"name": "Amplification", "text": "[8mult] next link", "image": None,
        "image_data": {**PALETTE, "departure": 0.055, "background": BACKGROUND}}
LEGACY = {"name": "Amplification", "text": "[8mult] next link", "image": None,
          "image_data": dict(PALETTE)}


def _with_background(**changes):
    return {**RITE, "image_data": {**RITE["image_data"],
                                   "background": {**BACKGROUND, **changes}}}


def _png(data: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), np.float32)


# ---------------------------------------------------------------------------
# Which rites take the procedural path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("image_data", [
    None, {}, dict(PALETTE), {"background": {}}, {"background": "circle"},
    {"background": {**BACKGROUND, "version": 2}},
])
def test_no_readable_background_keeps_the_legacy_pattern(image_data):
    """RiteVisuals.background_of: missing, malformed or an unreadable version
    all draw the legacy pattern, never nothing."""
    assert not RB.has_background({"name": "Echo", "image_data": image_data})


def test_a_background_replaces_the_baked_pattern():
    """The regression: a rite with a background rendered exactly like one without."""
    assert RB.has_background(RITE)
    new = _png(fate_render.render_rite(RITE, sheen=False)[0])
    old = _png(fate_render.render_rite(LEGACY, sheen=False)[0])
    assert new.shape != old.shape or np.abs(new - old).mean() > 5.0


def test_the_background_is_what_is_drawn():
    """Changing only the waves changes the render: the look is read, not just
    detected."""
    a = _png(fate_render.render_rite(RITE, sheen=False)[0])
    b = _png(fate_render.render_rite(_with_background(modes=[[2, 4], [1, 5], [3, 3], [2, 6]]),
                                     sheen=False)[0])
    assert a.shape == b.shape and np.abs(a - b).mean() > 5.0


def test_the_face_keeps_the_card_silhouette():
    face = fate_render._rite_face(RITE)
    assert face.size == (CARD_W, CARD_H)
    alpha = np.asarray(face)[..., 3]
    assert alpha[0, 0] == 0 and alpha[CARD_H // 2, CARD_W // 2] == 255


def test_render_cache_misses_for_the_old_renderer():
    """Renders cached before this port hold the legacy look under the SAME
    image_data, so only the renderer version can retire them."""
    assert art_cache.RENDERER_VERSION != "2026-09-25.1"


# ---------------------------------------------------------------------------
# The frame
# ---------------------------------------------------------------------------

def _wave_point(px, py, look, rect):
    """Where art_field samples the waves for viewport pixel (px, py) in `rect`."""
    scale, center = RB.wave_mapping(look, rect)
    ex = (px - rect["center"][0]) / rect["half"]
    ey = -(py - rect["center"][1]) / rect["half"]
    rot = float(look.get("rotation", 0.0))
    c, s = np.cos(rot), np.sin(rot)
    return ((c * ex - s * ey) / scale + center[0], (s * ex + c * ey) / scale + center[1])


def test_the_reference_frame_maps_to_itself():
    look = RB.resolve_background(BACKGROUND)
    scale, center = RB.wave_mapping(look, RB.REFERENCE_FRAME)
    assert scale == pytest.approx(0.98)
    assert center == pytest.approx((0.0, -1.6288))


@pytest.mark.parametrize("rotation", [0.0, 0.7])
def test_a_frame_switch_moves_only_the_outline(rotation):
    """RiteVisuals: placement is read against the reference square whatever the
    frame, so every pixel lands on the same point of the waves in both."""
    look = RB.resolve_background({**BACKGROUND, "rotation": rotation})
    for px, py in [(10, 20), (280, 280), (500, 700)]:
        card = _wave_point(px, py, look, RB.FRAMES["card"])
        full = _wave_point(px, py, look, RB.FRAMES["full"])
        assert full == pytest.approx(card)


def test_an_unknown_frame_is_the_card_frame():
    assert RB.frame_of({}) == "card"
    assert RB.frame_of({"frame": "poster"}) == "card"
    assert RB.frame_of({"frame": "full"}) == "full"


# ---------------------------------------------------------------------------
# The palette
# ---------------------------------------------------------------------------

def test_palette_is_the_rites_own():
    assert RB.palette(RITE) == ((26, 15, 74), (255, 176, 31), (255, 202, 57), True)


def test_one_colour_of_the_pair_stands_in_for_the_other():
    """RiteVisuals._apply_colors: a flat fill, not the shader default."""
    rite = {"image_data": {"secondary_color": "#112233"}}
    assert RB.palette(rite) == ((200, 20, 20), (17, 34, 51), (17, 34, 51), True)


def test_no_pair_keeps_the_act_ladder():
    bg, primary, secondary, custom = RB.palette({"image_data": {}})
    assert (primary, secondary, custom) == ((81, 158, 34), (100, 143, 50), False)


def test_two_tone_fill_paints_blobs_flat():
    """Any fill but "off" paints each blob one of the pair, with no shimmer
    between them: every opaque pixel well inside a blob is one of the three."""
    face = RB.RiteBackground(_with_background(fill={"base": "sign"})).frame(0.0)
    rgb = np.asarray(face)[..., :3].reshape(-1, 3)
    colours = {tuple(c) for c in np.unique(rgb, axis=0)}
    assert {(26, 15, 74), (255, 176, 31), (255, 202, 57)} <= colours


def test_the_shimmer_blends_the_pair():
    face = RB.RiteBackground(RITE).frame(0.0)
    rgb = np.asarray(face)[..., :3].reshape(-1, 3).astype(int)
    # Strictly between primary and secondary on green: the shimmer, not zones.
    between = (rgb[:, 1] > 180) & (rgb[:, 1] < 199) & (rgb[:, 0] == 255)
    assert between.sum() > 1000


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

def test_the_loop_closes():
    """The last frame hands over to the first: the cross-fade blends toward the
    frame before the start."""
    frames = RB.RiteBackground(RITE).frames(duration=2.0, fps=10)
    first, last = (np.asarray(f, np.float32) for f in (frames[0], frames[-1]))
    middle = np.asarray(frames[len(frames) // 2], np.float32)
    assert np.abs(first - last).mean() < np.abs(first - middle).mean()


def test_a_background_rite_renders_animated():
    assert fate_render.render_rite_gif(RITE, fps=5, sheen=False)[:6] == b"GIF89a"
