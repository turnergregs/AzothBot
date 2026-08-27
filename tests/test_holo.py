"""`azoth_logic/holo.py` — the upgraded card's holographic sheen.

A port of the holographic block in the game's `base_card_shader.gdshader`.
EVERY card scene wears it -- card.tscn, aspect_card.tscn and event_card.tscn all
load `base_card_material.tres`, where `_enableHolographic = true` at intensity
`0.06`; an upgraded card is raised to `0.15`.

The tilt that drives it in-game has no analogue in a rendered image, so the
spectrum is swept by a phase instead. What these tests pin is the part that DID
port: the rainbow, and the `holoAppeal` mask that decides which pixels shimmer —
in particular that white stays suppressed, because the rules text is white and a
shimmering block of text is unreadable.
"""
import numpy as np
import pytest
from PIL import Image

from azoth_logic import holo


def _solid(colour, size=(40, 60), alpha=255):
    return Image.new("RGBA", size, tuple(colour) + (alpha,))


def _rgb(image):
    return np.asarray(image).astype(np.float32)[..., :3]


def test_zero_intensity_changes_nothing():
    face = _solid((200, 80, 40))
    assert np.array_equal(np.asarray(holo.apply(face, intensity=0.0)), np.asarray(face))


def test_the_result_is_rgba_of_the_same_size():
    face = _solid((200, 80, 40))
    out = holo.apply(face)
    assert out.mode == "RGBA" and out.size == face.size


def test_alpha_is_untouched():
    face = _solid((200, 80, 40), alpha=128)
    assert np.array_equal(np.asarray(holo.apply(face))[..., 3],
                          np.asarray(face)[..., 3])


def test_transparent_pixels_are_left_alone():
    """The sheen is masked by alpha, so the rim outside the card cannot pick up
    a rainbow fringe — which `to_gif` would then have to quantise."""
    face = _solid((200, 80, 40), alpha=0)
    assert np.array_equal(_rgb(holo.apply(face)), _rgb(face))


def test_a_saturated_colour_shimmers():
    face = _solid((220, 40, 40))
    assert not np.array_equal(_rgb(holo.apply(face)), _rgb(face))


def test_white_is_suppressed_relative_to_colour():
    """`holoAppeal` deliberately keeps white out of range. The rules text and
    the card name are white; if they shimmered the card would be unreadable."""
    def moved(colour):
        face = _solid(colour)
        return float(np.abs(_rgb(holo.apply(face)) - _rgb(face)).mean())

    assert moved((255, 255, 255)) < moved((220, 40, 40))


def test_black_is_below_the_threshold():
    face = _solid((0, 0, 0))
    assert np.array_equal(_rgb(holo.apply(face)), _rgb(face))


def test_the_phase_moves_the_bands():
    face = _solid((220, 40, 40))
    a = _rgb(holo.apply(face, phase=0.0))
    b = _rgb(holo.apply(face, phase=1.5))
    assert not np.array_equal(a, b)


def test_the_sweep_covers_one_full_loop():
    """A phase of exactly 2*pi is the same image as 0, so a GIF that ends there
    joins back onto its own first frame."""
    face = _solid((220, 40, 40))
    a = _rgb(holo.apply(face, phase=0.0))
    b = _rgb(holo.apply(face, phase=2 * np.pi))
    assert np.abs(a - b).max() <= 1.0


def test_apply_all_returns_one_frame_per_input():
    frames = [_solid((220, 40, 40)) for _ in range(6)]
    assert len(holo.apply_all(frames)) == 6


def test_apply_all_advances_the_phase():
    frames = [_solid((220, 40, 40)) for _ in range(6)]
    out = holo.apply_all(frames)
    assert not np.array_equal(_rgb(out[0]), _rgb(out[3]))


def test_a_single_frame_still_gets_the_sheen():
    """16 of the 197 comparisons animate on neither side. A fixed-phase sheen is
    how a foil card photographs — still a marker, it just does not travel."""
    face = _solid((220, 40, 40))
    out = holo.apply_all([face])
    assert not np.array_equal(_rgb(out[0]), _rgb(face))


@pytest.mark.parametrize("intensity", [0.15, 0.3, 1.0])
def test_output_stays_in_range(intensity):
    """The metallic blend multiplies, so it can overshoot without the clamp."""
    arr = np.asarray(holo.apply(_solid((250, 250, 10)), intensity=intensity))
    assert arr.min() >= 0 and arr.max() <= 255


# ---------------------------------------------------------------------------
# The constants
# ---------------------------------------------------------------------------

def test_the_constants_come_from_the_material_not_the_shader_defaults():
    """REGRESSION. The first version of this module took the shader's DECLARED
    uniform defaults (threshold 0.15, saturationWeight 1.5, metallicness 0.7).
    `base_card_material.tres` overrides all six, and with the defaults the sheen
    was invisible on exactly the white faces it matters most for -- catalysts
    rendered flat white in Discord while shimmering in-game.
    """
    assert holo.HOLO_INTENSITY == 0.06        # base_card_material.tres
    assert holo.UPGRADED_INTENSITY == 0.15    # set_upgrade_card_visuals
    assert holo.BRIGHTNESS_THRESHOLD == 0.1
    assert holo.SATURATION_WEIGHT == 2.0
    assert holo.METALLICNESS == 3.0


def test_metallicness_is_an_extrapolation_not_a_blend():
    """`_metallicness = 3.0` is outside [0, 1] on purpose. GLSL `mix`
    extrapolates, overshooting toward the reflection — which is the whole reason
    a white catalyst picks up colour at an intensity of 0.06. Clamping it to a
    normal blend is the bug this guards."""
    assert holo.METALLICNESS > 1.0

    face = _solid((250, 250, 250))
    blended = float(np.abs(_rgb(holo.apply(face, metallicness=1.0)) - _rgb(face)).mean())
    extrapolated = float(np.abs(_rgb(holo.apply(face)) - _rgb(face)).mean())
    assert extrapolated > blended


def test_a_white_catalyst_face_picks_up_colour():
    """The reported bug: catalysts are colourless, so their art and border are
    white, and white is nearly all a catalyst has. If white comes back white the
    card is flat."""
    face = _solid((245, 245, 245))
    out = _rgb(holo.apply(face))
    # Not just brightened -- the channels have to separate.
    spread = float(np.abs(out[..., 0] - out[..., 2]).max())
    assert spread > 2.0, "white should tint, not just shift"
