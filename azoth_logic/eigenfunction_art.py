"""Card art from eigenfunction `.exr` files.

A faithful port of `scenes/cards/split_card_image.gdshader` in the azoth repo,
which is what the game uses to draw animated card art. The EXR's three colour
channels are three eigenfunction MODES of a vibrating domain; the shader
superposes them with time-varying weights and thresholds the result:

    Z     = ef.r + w1*ef.g + w2*ef.b        # w1, w2 driven by TIME
    alpha = smoothstep(thr - fwidth(Z), thr + fwidth(Z), abs(Z))
    color = secondary_color if ef.a > 0.75 else primary_color

So the alpha channel is a *zone map*, not opacity: 0.5 marks the base zone and
1.0 the accent zone. Setting w1 = w2 = 0 gives the static frame.

Only cards whose `image` ends in `.exr` use this path -- roughly 246 of 400.
The rest carry a plain PNG and are drawn without any of it, matching
`ImageCache.eigenfunction_name_for_image()`.
"""
from __future__ import annotations

import os

# Must be set before cv2 is imported: OpenCV gates its EXR codec behind it.
os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

# split_card_image.gdshader, `const float threshold_norm`.
THRESHOLD = 0.0005

# GlobalVars.EIGENFUNCTION_DEPARTURE. How far the two secondary modes are allowed
# to pull the shape. A card may override it via `image_data.departure`.
# NOTE the 0.05 baked into animated_card_image.tres is only the editor default --
# the game overwrites it from GlobalVars on every card.
DEFAULT_DEPARTURE = 0.15

# The Image node in scenes/cards/card.tscn, and the EXRs' native size.
ART_SIZE = (275, 275)

# Colour zones. For a card the accent (A = 1.0) takes the element colour and the
# base (A = 0.5) stays white -- see GlobalVars.get_eigenfunction_colors().
ELEMENT_COLORS = {
    "blood": (255, 0, 0),
    "sol": (249, 164, 16),
    "anima": (135, 105, 233),
}
BASE_COLOR = (255, 255, 255)


def _organic(t, f1, f2, f3, phase_amp):
    """Phase-modulated multi-frequency sine, bounded in [-1, 1].

    `float organic(float t, float f1, float f2, float f3, float phase_amp)`.
    """
    return np.sin(t * f1 + np.sin(t * f2) * phase_amp) * np.cos(t * f3)


def mode_weights(t: float, departure: float = DEFAULT_DEPARTURE):
    """The two secondary-mode weights at time `t`, in seconds.

    Frequencies are taken verbatim from the shader. They are deliberately
    incommensurate so the motion never repeats -- which is exactly why an
    animation needs `frames()`' cross-fade to close a loop.
    """
    w1 = departure * _organic(t, 1.000, 0.370, 0.710, 1.5)
    w2 = departure * _organic(t, 1.310, 0.530, 0.890, 1.3)
    return w1, w2


def _smoothstep(edge0, edge1, x):
    t = np.clip((x - edge0) / np.maximum(edge1 - edge0, 1e-12), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def load_exr(path):
    """Read an eigenfunction EXR as (r, g, b, a) float32 planes.

    OpenCV hands back BGRA; the shader talks in RGBA, so unpack to the shader's
    names and keep the rest of this module readable against it.
    """
    raw = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if raw is None:
        raise ValueError(f"could not read EXR: {path}")
    if raw.ndim != 3 or raw.shape[2] < 4:
        raise ValueError(f"EXR has no alpha zone map: {path} shape={raw.shape}")
    b, g, r, a = raw[..., 0], raw[..., 1], raw[..., 2], raw[..., 3]
    return r.astype(np.float32), g.astype(np.float32), b.astype(np.float32), a.astype(np.float32)


def _shade(field, zone, primary, secondary):
    """One frame: threshold the field, colour it by zone. Returns RGBA uint8."""
    # The shader uses fwidth(Z) -- the field's own screen-space gradient -- so the
    # edge stays ~1px wide wherever it lands, however steep the eigenfunction is
    # locally. |dZ/dx| + |dZ/dy| is fwidth's definition.
    gy, gx = np.gradient(field)
    softness = np.maximum(np.abs(gx) + np.abs(gy), 1e-6)
    alpha = _smoothstep(THRESHOLD - softness, THRESHOLD + softness, np.abs(field))

    rgb = np.where(
        (zone > 0.75)[..., None],
        np.asarray(secondary, np.float32),
        np.asarray(primary, np.float32),
    )
    return np.dstack([rgb, alpha * 255.0]).astype(np.uint8)


def colors_for_card(card: dict):
    """(primary, secondary) for a card, i.e. (base zone, accent zone).

    From GlobalVars.get_eigenfunction_colors():

      * `secondary` (accent, alpha 1.0) is always the card's element colour.
      * `primary` (base, alpha 0.5) is white -- EXCEPT on a split card, where it
        takes the split element's colour. A split card's art is two-toned, one
        element per zone, which is how it reads as split at a glance.

    A card with no element -- 64 of 400 -- comes out white on white, matching
    what the game shows for colourless cards.
    """
    element = (card.get("element") or "").lower()
    secondary = ELEMENT_COLORS.get(element, BASE_COLOR)

    # `is Dictionary` and non-empty, not just presence: every card exported from
    # the database carries an explicit "split": null, so a truthiness check on
    # the key alone treats single-sided cards as split.
    split = card.get("split")
    if isinstance(split, dict) and split:
        primary = ELEMENT_COLORS.get(str(split.get("element", "")).lower(), BASE_COLOR)
    else:
        primary = BASE_COLOR
    return primary, secondary


def departure_for_card(card: dict) -> float:
    """Per-card `departure` override, mirroring GlobalVars.get_eigenfunction_departure."""
    image_data = card.get("image_data")
    if isinstance(image_data, dict):
        value = image_data.get("departure")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return DEFAULT_DEPARTURE


def still(exr_path, primary=BASE_COLOR, secondary=BASE_COLOR) -> Image.Image:
    """The t = 0 frame, where both secondary modes drop out and Z = ef.r."""
    r, g, b, a = load_exr(exr_path)
    return Image.fromarray(_shade(r, a, primary, secondary), "RGBA")


def frames(exr_path, primary=BASE_COLOR, secondary=BASE_COLOR,
           duration=4.0, fps=15, departure=DEFAULT_DEPARTURE,
           crossfade=0.25) -> list:
    """A seamless animated loop, as a list of RGBA frames.

    The shader's frequencies are incommensurate on purpose -- searching 2-60s
    finds no duration where the motion returns to its starting state (the best,
    at 48.2s, still lands ~30% of the weight range away). An animation has to
    loop anyway, so the last `crossfade` fraction blends toward the frame BEFORE
    the start.

    The blend happens in FIELD space -- on Z, before thresholding -- not on
    rendered pixels. Z is smooth, so interpolating it slides the shape's edge;
    interpolating the output would cross-dissolve two hard-edged images and
    ghost. The shape stays crisp for the whole blend.
    """
    r, g, b, a = load_exr(exr_path)
    total = max(1, int(round(duration * fps)))
    fade_start = int(total * (1.0 - crossfade))

    def field(t):
        w1, w2 = mode_weights(t, departure)
        return r + w1 * g + w2 * b

    out = []
    for i in range(total):
        t = i / fps
        z = field(t)
        if i >= fade_start and total > fade_start:
            s = (i - fade_start) / (total - fade_start)
            s = s * s * (3.0 - 2.0 * s)          # ease the handover
            z = (1.0 - s) * z + s * field(t - duration)
        out.append(Image.fromarray(_shade(z, a, primary, secondary), "RGBA"))
    return out
