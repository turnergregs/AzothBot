"""A rite's procedural background: `image_data.background`.

Since 2026-09-25 a rite's look can be a look in card art's format (the three
eigenfunction families, a silhouette, gaps, warp, placement, a fill rule) under
`image_data.background`, drawn by the same `art_field()` card art uses. The
game's `RiteVisuals` (scripts/helpers/rite_visuals.gd) reads it and
`reactant_card.gdshader`'s `procedural` branch draws it; see the azoth repo's
docs/PROCEDURAL_ART.md § The rite adapter. This module is those two, reusing
`procedural_art.Field` for the field itself (verified against the GPU there).

What is rite-specific, and ported here:

  * **The frame.** The art is drawn in a square fixed on the card, picked by the
    background's `frame` (FRAMES: the card's top square, or the legacy look's
    full-art circle, far bigger than the card and centred above it). The
    silhouette is inscribed in that square; the look's scale, centre and
    rotation are read against REFERENCE_FRAME and converted for the frame drawn
    (`wave_mapping`).
  * **The colour.** Not card art's zones: `fill` "off" (the default here, where
    card art's is "sign") is the shimmer, `mix(primary, secondary, cp)` with
    `cp` the shader's radial wave around the card; any other fill paints each
    blob flat in one of the pair. Outside the pattern is `background_color`.

A rite with no `background` (or one of a version this port does not read) is
not drawn here: `fate_render` keeps the legacy baked pattern for it, as the game
keeps its legacy shader path.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from azoth_logic import procedural_art as pa
from azoth_logic.card_layout import CARD_W, CARD_H
from azoth_logic.fate_layout import parse_color

# RiteVisuals constants.
BACKGROUND_KEY = "background"
DEFAULT_DEPARTURE = 0.05
DEFAULT_FILL = {"base": "off"}
DEFAULT_FRAME = "card"
# Centre and half-side of each frame's square, in the card SubViewport's pixels
# (560 x 897). Only the card sizes: the Rites bar's bigger frames never reach
# /render.
FRAMES = {
    "card": {"center": (280.0, 280.0), "half": 280.0},
    "full": {"center": (280.0, -221.7), "half": 878.8},
}
REFERENCE_FRAME = FRAMES["card"]
# rite_card.tscn's Background node, which the shader's UV spans: it overhangs
# the 560-wide card by 50 px on each side.
BACKGROUND_NODE_SIZE = (660.014, 897.6)
BACKGROUND_NODE_ORIGIN = (-50.007, -0.3)

# reactant_card.gdshader: the procedural branch's threshold, and the uniforms
# attribute_rite.tres (the material every live rite wears) leaves in force.
ART_THRESHOLD = 0.0005
TIME_OFFSET = 0.5
DEFAULT_BACKGROUND_COLOR = (200, 20, 20)
# The act ladder's act-1 pair, for a rite that authors neither colour.
ACT1_PAIR = ((81, 158, 34), (100, 143, 50))
# The shimmer's centre: the material's one SDF object at (0, 0), projected as
# the shader does (`x / 3.5 + 0.5`, `y + 0.57`), and its aspect correction.
SHIMMER_CENTER = (0.5, 0.57)
SHIMMER_ASPECT = 1.0 / 1.5

# The card's silhouette: the alpha of the vendored full-viewport rite export,
# which carries the rounded rectangle at its final position.
SILHOUETTE_FILE = (Path(__file__).resolve().parent.parent / "assets" / "card_art"
                   / "backgrounds" / "rite_background_attribute_mask.png")
_silhouette = None


# ---------------------------------------------------------------------------
# Reading a background (RiteVisuals)
# ---------------------------------------------------------------------------

def background_of(rite: dict) -> dict:
    """The rite's `image_data.background`, or {} when it has none, it is
    malformed, or its version is not one this port reads (RiteVisuals
    .background_of, which then draws the legacy pattern)."""
    image_data = rite.get("image_data") if isinstance(rite, dict) else None
    if not isinstance(image_data, dict):
        return {}
    background = image_data.get(BACKGROUND_KEY)
    if not isinstance(background, dict) or not background:
        return {}
    try:
        version = int(background.get("version", pa.VERSION))
    except (TypeError, ValueError):
        return {}
    return background if version == pa.VERSION else {}


def has_background(rite: dict) -> bool:
    return bool(background_of(rite))


def resolve_background(background: dict) -> dict:
    """DEFAULT_FILL under whatever the look sets, then ArtVisuals.resolve."""
    out = {"fill": dict(DEFAULT_FILL)}
    out.update(background)
    return pa.resolve(out)


def frame_of(background: dict) -> str:
    picked = str(background.get("frame", DEFAULT_FRAME))
    return picked if picked in FRAMES else DEFAULT_FRAME


def wave_mapping(look: dict, rect: dict):
    """(scale, center) for the shader, from a look read against REFERENCE_FRAME
    and drawn in `rect` (RiteVisuals.wave_mapping): pixels per wave unit kept,
    the centre moved by the frame's offset from the reference, turned by the
    rotation."""
    pixels_per_unit = REFERENCE_FRAME["half"] * max(float(look.get("scale", 1.0)), 0.001)
    mx = rect["center"][0] - REFERENCE_FRAME["center"][0]
    my = -(rect["center"][1] - REFERENCE_FRAME["center"][1])
    rot = float(look.get("rotation", 0.0))
    c, s = np.cos(rot), np.sin(rot)
    shift = ((c * mx - s * my) / pixels_per_unit, (s * mx + c * my) / pixels_per_unit)
    center = look.get("center")
    cx, cy = (float(center[0]), float(center[1])) \
        if isinstance(center, list) and len(center) >= 2 else (0.0, 0.0)
    return pixels_per_unit / rect["half"], (cx + shift[0], cy + shift[1])


def departure_of(rite: dict) -> float:
    value = (rite.get("image_data") or {}).get("departure", DEFAULT_DEPARTURE)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return DEFAULT_DEPARTURE


def palette(rite: dict):
    """(background, primary, secondary), as RiteVisuals._apply_colors sets them:
    one authored colour of the pair stands in for the other (a flat fill), and
    neither leaves the act ladder's act-1 pair."""
    data = rite.get("image_data") or {}
    bg = parse_color(data.get("background_color")) or DEFAULT_BACKGROUND_COLOR
    primary = parse_color(data.get("primary_color"))
    secondary = parse_color(data.get("secondary_color"))
    if primary is None and secondary is None:
        return bg, ACT1_PAIR[0], ACT1_PAIR[1], False
    primary = primary or secondary
    secondary = secondary or primary
    return bg, primary, secondary, True


# ---------------------------------------------------------------------------
# Drawing it (reactant_card.gdshader's procedural branch)
# ---------------------------------------------------------------------------

def _silhouette_alpha() -> np.ndarray:
    global _silhouette
    if _silhouette is None:
        img = Image.open(SILHOUETTE_FILE).convert("RGBA")
        if img.size != (CARD_W, CARD_H):
            img = img.resize((CARD_W, CARD_H), Image.LANCZOS)
        _silhouette = np.asarray(img, np.float32)[..., 3]
    return _silhouette


def _smoothstep(edge0, edge1, x):
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


class RiteBackground:
    """One rite's background over the whole card viewport. Everything static is
    computed once; `frame(t)` is one RGBA frame at CARD_W x CARD_H."""

    def __init__(self, rite: dict):
        background = background_of(rite)
        if not background:
            raise ValueError(f"rite {rite.get('name')!r} has no readable background")
        look = resolve_background(background)
        rect = FRAMES[frame_of(background)]

        # Pixel centres of the viewport.
        px, py = np.meshgrid(np.arange(CARD_W, dtype=np.float64) + 0.5,
                             np.arange(CARD_H, dtype=np.float64) + 0.5)
        # The frame's quad UV. `(UV - art_quad.xy) / (2 * art_quad.zw) + 0.5`
        # with UV in the Background node: the node's origin and size cancel.
        qu = (px - rect["center"][0]) / (2.0 * rect["half"]) + 0.5
        qv = (py - rect["center"][1]) / (2.0 * rect["half"]) + 0.5
        self.field = pa.Field(look, 0, departure_of(rite), ART_THRESHOLD,
                              uv=(qu, qv), placement=wave_mapping(look, rect))

        # The Background node's own UV, for the shimmer's radial wave.
        u = (px - BACKGROUND_NODE_ORIGIN[0]) / BACKGROUND_NODE_SIZE[0]
        v = (py - BACKGROUND_NODE_ORIGIN[1]) / BACKGROUND_NODE_SIZE[1]
        dx, dy = u - SHIMMER_CENTER[0], v - SHIMMER_CENTER[1]
        self._shimmer_dist = np.sqrt(dx * dx * SHIMMER_ASPECT ** 2 + dy * dy)

        bg, primary, secondary, custom = palette(rite)
        self.bg = np.asarray(bg, np.float32)
        self.primary = np.asarray(primary, np.float32)
        self.secondary = np.asarray(secondary, np.float32)
        # The shader only paints zones with the rite's own pair.
        self.zoned = custom and self.field.look.fill != 0
        self.alpha = _silhouette_alpha()

    def field_at(self, t: float) -> np.ndarray:
        return self.field.at(t + TIME_OFFSET)

    def shimmer_at(self, t: float) -> np.ndarray:
        """combinedPattern: how far toward secondary the shimmer is here."""
        return 0.5 + 0.5 * np.cos(self._shimmer_dist * 3.0 - t)

    def shade(self, z: np.ndarray, cp: np.ndarray) -> Image.Image:
        gy, gx = np.gradient(z)
        fw = np.maximum(np.abs(gx) + np.abs(gy), 1e-9)
        pattern = _smoothstep(ART_THRESHOLD - fw, ART_THRESHOLD + fw, np.abs(z))[..., None]
        if self.zoned:
            secondary = (self.field.zone(z) > 0.75)[..., None]
            col = np.where(secondary, self.secondary, self.primary)
        else:
            cp = cp[..., None]
            col = self.primary * (1.0 - cp) + self.secondary * cp
        rgb = self.bg * (1.0 - pattern) + col * pattern
        out = np.dstack([np.clip(rgb, 0, 255), self.alpha])
        return Image.fromarray(out.round().astype(np.uint8), "RGBA")

    def frame(self, t: float = 0.0) -> Image.Image:
        return self.shade(self.field_at(t), self.shimmer_at(t))

    def frames(self, duration: float = 4.0, fps: int = 15, crossfade: float = 0.25) -> list:
        """A seamless loop. Neither the wobble nor the shimmer is periodic over
        a short loop, so the last `crossfade` blends toward the frame before the
        start, both in value space before thresholding (both are smooth), as
        eigenfunction_art._animate does for card art."""
        total = max(1, int(round(duration * fps)))
        fade_start = int(total * (1.0 - crossfade))
        out = []
        for i in range(total):
            t = i / fps
            z, cp = self.field_at(t), self.shimmer_at(t)
            if i >= fade_start:
                s = (i - fade_start) / (total - fade_start)
                s = s * s * (3.0 - 2.0 * s)
                z = (1.0 - s) * z + s * self.field_at(t - duration)
                cp = (1.0 - s) * cp + s * self.shimmer_at(t - duration)
            out.append(self.shade(z, cp))
        return out
