"""The holographic sheen every card wears.

A port of the holographic block in `scenes/cards/base_card_shader.gdshader`
(game repo, ~line 947) and the `metallicReflection` rainbow it calls.

It is on for EVERY card: `card.tscn`, `aspect_card.tscn` and `event_card.tscn`
all load `base_card_material.tres`, which sets `_enableHolographic = true` at
`_holoIntensity = 0.06`. An upgraded card is raised to `0.15` by
`base_card.gd::set_upgrade_card_visuals`.

A colourless card -- a catalyst -- is mostly this effect. It gets the white
border and white art, and nothing else colours it.

WHAT PORTS AND WHAT CANNOT

The rainbow itself ports exactly: a spectrum value fed through three sines at
0, 2pi/3 and 4pi/3, then saturation-boosted by `mix(gray, color, 1.5)` around a
`dot(color, vec3(0.633))` grey. So does the mask -- the `holoAppeal` metric that
decides which pixels shimmer at all, including its special handling for greys
(boosted) versus white (suppressed).

What cannot port is the part that made this effect exist: in-game the spectrum
comes from a reflection vector off a surface normal tilted by where the card is
on screen and where the pointer is. There is no tilt in a rendered image, so
that term is constant and the rainbow would be a frozen gradient.

It is replaced by a PHASE that advances once around the animation loop. The
motion the player gets from tilting the card, a viewer gets from the GIF playing.
A still comparison keeps the sheen at a fixed phase, which is how a foil card
photographs -- the marker is still there, it just does not travel.

`_fresnelPower` is likewise a view-angle term with no analogue here. Its visible
consequence -- stronger toward the edges -- is kept as a radial falloff, because
a foil that shimmers hardest in the middle of the card reads as a smudge.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

# From `scenes/cards/base_card_material.tres` -- the material `card.tscn`
# actually puts on every card -- NOT from the shader's declared uniform
# defaults, which it overrides on all six of these.
#
# That distinction cost a bug: the first version of this file took the uniform
# defaults (threshold 0.15, saturationWeight 1.5, metallicness 0.7) and the
# sheen was invisible on the white faces where it matters most.
#
# `_metallicness = 3.0` is deliberate and outside [0, 1]. GLSL `mix` extrapolates,
# so `mix(tinted, reflection, 3.0)` overshoots hard toward the rainbow. It is the
# reason a white catalyst picks up colour at an intensity of 0.06.
HOLO_INTENSITY = 0.06          # base_card_material.tres, every card
UPGRADED_INTENSITY = 0.15      # base_card.gd::set_upgrade_card_visuals
BRIGHTNESS_THRESHOLD = 0.1
SATURATION_WEIGHT = 2.0
METALLICNESS = 3.0

# How many rainbow bands cross the card. In-game this falls out of
# `_holoScale = 4.0` applied to the reflection vector; with no reflection to
# scale, it is set directly, and tracks that value. Wide bands on purpose: a
# band narrower than a glyph turns the rules text into confetti.
BAND_FREQUENCY = 4.0


# The coordinate grids depend only on the frame SIZE, and a 60-frame loop hands
# in 60 frames of one size, so they are built once per size.
#
# Measured honestly: this is NOT where the time goes. At 560x897 a full apply is
# ~70ms, of which the rainbow's three sines are ~17ms, the saturation min/max
# ~11ms and the smoothsteps ~6ms; rebuilding the grids was inside the noise.
# Kept because it is small and correct, not because it bought anything.
_geometry_cache: dict = {}


def _geometry(height: int, width: int):
    """`(u, v, radial)` for a frame of this size, built once per size."""
    key = (height, width)
    cached = _geometry_cache.get(key)
    if cached is None:
        ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
        u = xs / max(width - 1, 1)
        v = ys / max(height - 1, 1)
        radial = np.clip(np.sqrt((u - 0.5) ** 2 + (v - 0.5) ** 2) / 0.7071, 0.0, 1.0)
        cached = (u, v, radial)
        # One entry per distinct face size; the renderers use a handful.
        if len(_geometry_cache) > 16:
            _geometry_cache.clear()
        _geometry_cache[key] = cached
    return cached


def _smoothstep(edge0: float, edge1: float, x):
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _rainbow(spectrum):
    """`metallicReflection`'s spectrum -> colour, transcribed."""
    r = np.sin(spectrum) * 0.5 + 0.5
    g = np.sin(spectrum + 2.094) * 0.5 + 0.5      # 2pi/3
    b = np.sin(spectrum + 4.189) * 0.5 + 0.5      # 4pi/3
    colour = np.stack([r, g, b], axis=-1)
    grey = (colour * 0.633).sum(axis=-1, keepdims=True)
    return np.clip(grey + (colour - grey) * 1.5, 0.0, 1.0)


def apply(frame: Image.Image, phase: float = 0.0,
          intensity: float = HOLO_INTENSITY,
          metallicness: float = METALLICNESS) -> Image.Image:
    """One RGBA frame with the sheen composited over its opaque pixels.

    `phase` should advance by 2*pi across an animation loop, so the sweep is
    seamless where the loop is.
    """
    arr = np.asarray(frame).astype(np.float32) / 255.0
    rgb, alpha = arr[..., :3], arr[..., 3]

    u, v, radial = _geometry(*rgb.shape[:2])

    brightness = rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    max_c, min_c = rgb.max(axis=-1), rgb.min(axis=-1)
    saturation = np.where(max_c > 0.0, (max_c - min_c) / np.maximum(max_c, 1e-6), 0.0)

    # `holoAppeal`: which pixels are worth shimmering. Greys get boosted into
    # range, white stays suppressed -- otherwise the rules text carries the
    # effect and the card becomes unreadable.
    achromatic = 1.0 - np.clip(saturation * 10.0, 0.0, 1.0)
    is_grey = (achromatic
               * _smoothstep(0.9, 0.75, brightness)
               * _smoothstep(0.3, 0.45, brightness))
    saturation_multiplier = 0.3 + (SATURATION_WEIGHT - 0.3) * np.clip(saturation + 0.1, 0.0, 1.0)
    appeal = brightness * saturation_multiplier
    appeal = np.maximum(appeal, saturation ** 2 * 0.8)
    appeal = appeal + (brightness * 0.8 - appeal) * is_grey
    appeal = np.maximum(appeal, saturation ** 2 * 0.8)

    spectrum = (u + v) * BAND_FREQUENCY + phase
    reflection = _rainbow(spectrum)

    strength = _smoothstep(BRIGHTNESS_THRESHOLD, BRIGHTNESS_THRESHOLD + 0.4, appeal)
    credential = np.maximum(saturation, is_grey * brightness * 0.7)
    strength = strength * (0.5 + 0.5 * credential)

    # `radial` stands in for the fresnel term: brighter toward the edges.
    strength = strength * intensity * (0.3 + 0.7 * radial)

    # Below the threshold the shader does not enter the block at all.
    strength = np.where(appeal > BRIGHTNESS_THRESHOLD, strength, 0.0)
    strength = (strength * alpha)[..., None]

    colour_intensity = rgb.max(axis=-1)
    boost = 1.0 + (colour_intensity * 2.0 - 1.0) * (saturation * (1.0 - brightness))
    tinted = rgb * (1.0 + reflection * 0.5)
    replaced = reflection * boost[..., None]
    # mix(), including the extrapolation when metallicness > 1.
    metallic = tinted + (replaced - tinted) * metallicness

    out = np.clip(rgb + (metallic - rgb) * strength, 0.0, 1.0)
    merged = np.concatenate([out, alpha[..., None]], axis=-1)
    return Image.fromarray((merged * 255.0 + 0.5).astype(np.uint8), "RGBA")


def apply_all(frames: list, intensity: float = HOLO_INTENSITY) -> list:
    """The sheen swept once across a whole loop, so it ends where it began."""
    count = len(frames)
    return [apply(frame, 2.0 * np.pi * i / count, intensity)
            for i, frame in enumerate(frames)]
