"""Procedural card art: art evaluated from stored eigenfunction terms.

A port of the game's procedural art (azoth repo, docs/PROCEDURAL_ART.md). A
piece of content made in the game's Codex carries its art as numbers under
`image_data.art` instead of as an `.exr`:

    "image_data": {"departure": 0.05, "art": {
        "version": 1, "family": "triangle",
        "modes": [[2, 4], [1, 5]], "params": [[1.0, 0.0], [0.25, 0.0]],
        "is_even": [0, 0], "is_dirichlet": [1, 1],
        "gap": 0.2, "scale": 0.95, "center": [0.0, 0.866], "rotation": 0.0,
        "framing": "whole", "fill": {"base": "sign"}, "warp": 0.1, ...}}

and the game draws it with `art_field()` / `art_zone()` in
`assets/shaders/procedural_art.gdshaderinc`, the three wave families in
`assets/shaders/eigenfunctions.gdshaderinc`, and the uniforms
`ArtVisuals.apply_params` (`scripts/helpers/art_visuals.gd`) derives from the
look. This module is those three, in numpy, and nothing else: it hands
`eigenfunction_art` a field and a zone map exactly as an `.exr` does, so the
thresholding, the loop and the GIF encoding are shared.

**Where art_field is shaped to read like an .exr's field** (the gap rescale that
lands the threshold where the normalised field crosses `gap`, and the fade over
the last two pixels inside the domain's edge), this follows it line for line,
because the host threshold and its antialiasing depend on exactly that shape.

The circle family reads the game's own Bessel table
(`assets/shaders/bessel_lookup.exr`, vendored by tools/sync_assets.py) and
blends into the same asymptotic form the shader does, so the two agree rather
than each being right in its own way.

Parity with the GPU is pinned by tests/test_procedural_art.py against values
rendered from the real shader (tests/fixtures/procedural_art_reference.json).
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

# ArtVisuals.VERSION. An art dict of any other version draws nothing, as in the
# game, which warns and draws nothing rather than guess.
VERSION = 1

TRIANGLE, CIRCLE, SQUARE = "triangle", "circle", "square"
FAMILIES = (TRIANGLE, CIRCLE, SQUARE)
# EigenTerms.MAX_TERMS: the shader's array size.
MAX_TERMS = 6

# ArtVisuals constants.
DEFAULT_GAP = 0.2
DEFAULT_ZONE_RADIUS = 0.5
FAMILY_FRAMING = {
    TRIANGLE: {"scale": 0.95, "center": [0.0, 0.866]},
    CIRCLE: {"scale": 0.9, "center": [0.0, 0.0]},
    SQUARE: {"scale": 0.85, "center": [0.0, 0.0]},
}
FAMILY_NORM = {TRIANGLE: 4.4, CIRCLE: 1.0, SQUARE: 1.0}
DEFAULT_ART = {
    "version": VERSION,
    "family": TRIANGLE,
    "modes": [[2, 4], [1, 5]],
    "params": [[1.0, 0.0], [0.25, 0.0]],
    "is_even": [0, 0],
    "is_dirichlet": [1, 1],
    "gap": DEFAULT_GAP,
    "scale": 0.95,
    "center": [0.0, 0.866],
    "rotation": 0.0,
    "framing": "whole",
    "fill": {"base": "sign"},
}
FILL_BASES = ("off", "on", "sign", "sign_inverted", "centre", "centre_inverted")

# procedural_art.gdshaderinc: per-term wobble clocks, and the edge fade width.
_WOBBLE_F1 = (1.000, 1.310, 0.870, 1.130, 0.790, 1.230)
_WOBBLE_F2 = (0.370, 0.530, 0.610, 0.290, 0.470, 0.410)
_WOBBLE_F3 = (0.710, 0.890, 0.570, 0.930, 0.670, 0.830)
EDGE_PX = 2.0

# eigenfunctions.gdshaderinc: the Bessel table's range and switch point.
BESSEL_MAX_X = 25.0
BESSEL_SWITCH = 25.0
BESSEL_ZEROS = np.array([
    [2.4048, 5.5201, 8.6537, 11.7915, 14.9309, 18.0711, 21.2116, 24.3525],
    [3.8317, 7.0156, 10.1735, 13.3237, 16.4706, 19.6159, 22.7601, 25.9037],
    [5.1356, 8.4172, 11.6198, 14.7960, 17.9598, 21.1170, 24.2701, 27.4206],
    [6.3802, 9.7610, 13.0152, 16.2235, 19.4094, 22.5827, 25.7482, 28.9084],
    [7.5883, 11.0647, 14.3725, 17.6160, 20.8269, 24.0190, 27.1991, 30.3710],
    [8.7715, 12.3386, 15.7002, 18.9801, 22.2178, 25.4303, 28.6266, 31.8117],
    [9.9361, 13.5893, 17.0038, 20.3208, 23.5861, 26.8202, 30.0337, 33.2330],
    [11.0864, 14.8213, 18.2876, 21.6415, 24.9349, 28.1912, 31.4228, 34.6371],
    [12.2251, 16.0378, 19.5545, 22.9452, 26.2668, 29.5457, 32.7963, 36.0256],
    [13.3543, 17.2412, 20.8070, 24.2339, 27.5837, 30.8854, 34.1552, 37.4001],
    [14.4755, 18.4335, 22.0470, 25.5095, 28.8874, 32.2119, 35.5007, 38.7618],
    [15.5898, 19.6160, 23.2759, 26.7733, 30.1790, 33.5264, 36.8343, 40.1118],
    [16.6983, 20.7899, 24.4949, 28.0267, 31.4600, 34.8305, 38.1577, 41.4511],
])

BESSEL_TABLE_PATH = (Path(__file__).resolve().parent.parent
                     / "assets" / "card_art" / "bessel_lookup.exr")
_bessel_table = None


# ---------------------------------------------------------------------------
# Reading a look
# ---------------------------------------------------------------------------

def art_of(item: dict) -> dict:
    """The item's `image_data.art`, or {} when it has none, it is malformed, or
    its version is not one this port reads. Mirrors ArtVisuals.art_of."""
    image_data = item.get("image_data") if isinstance(item, dict) else None
    if not isinstance(image_data, dict):
        return {}
    art = image_data.get("art")
    if not isinstance(art, dict) or not art:
        return {}
    try:
        version = int(art.get("version", VERSION))
    except (TypeError, ValueError):
        return {}
    return art if version == VERSION else {}


def has_art(item: dict) -> bool:
    return bool(art_of(item))


def resolve(art: dict) -> dict:
    """DEFAULT_ART, then the look's family's framing, then the look itself
    (ArtVisuals.resolve)."""
    family = str(art.get("family", DEFAULT_ART["family"]))
    if family not in FAMILIES:
        family = TRIANGLE
    out = dict(DEFAULT_ART)
    out.update(FAMILY_FRAMING[family])
    out.update(art)
    out["family"] = family
    return out


def aligned_terms(look: dict):
    """The four term arrays at one length, at most MAX_TERMS (EigenTerms.aligned):
    a missing strength is 3, a missing flag the plain Dirichlet family."""
    def arr(key):
        v = look.get(key)
        return v if isinstance(v, list) else []
    modes, params, even, dirichlet = arr("modes"), arr("params"), arr("is_even"), arr("is_dirichlet")
    terms = []
    for i in range(min(len(modes), MAX_TERMS)):
        pair = modes[i] if isinstance(modes[i], list) and len(modes[i]) >= 2 else [1, 1]
        p = params[i] if i < len(params) and isinstance(params[i], list) and len(params[i]) >= 2 else [3, 0]
        terms.append({
            "k": float(pair[0]), "l": float(pair[1]),
            "amplitude": float(p[0]), "phase": float(p[1]),
            "is_even": float(even[i]) if i < len(even) else 0.0,
            "is_dirichlet": float(dirichlet[i]) if i < len(dirichlet) else 1.0,
        })
    return terms


def peak_amplitude(terms) -> float:
    return max((abs(t["amplitude"]) for t in terms), default=0.0)


def fill_index(look: dict) -> int:
    fill = look.get("fill")
    base = str(fill.get("base", "sign")) if isinstance(fill, dict) else "sign"
    return FILL_BASES.index(base) if base in FILL_BASES else FILL_BASES.index("sign")


class Look:
    """The uniforms ArtVisuals.apply_params pushes, as plain Python values."""

    def __init__(self, art: dict):
        look = resolve(art)
        self.family = look["family"]
        self.terms = aligned_terms(look)
        self.scale = float(look.get("scale", 1.0))
        center = look.get("center")
        self.center = (float(center[0]), float(center[1])) \
            if isinstance(center, list) and len(center) >= 2 else (0.0, 0.0)
        self.rotation = float(look.get("rotation", 0.0))
        self.clip = str(look.get("framing", "whole")) != "cropped"
        self.peak = peak_amplitude(self.terms)
        self.norm = max(self.peak, 0.001) * FAMILY_NORM.get(self.family, 1.0)
        self.gap = float(look.get("gap", DEFAULT_GAP))
        self.warp = float(look.get("warp", 0.0))
        self.warp_seed = float(look.get("warp_seed", 0.0))
        self.fill = fill_index(look)
        fill = look.get("fill")
        self.zone_radius = float(fill.get("radius", DEFAULT_ZONE_RADIUS)) \
            if isinstance(fill, dict) else DEFAULT_ZONE_RADIUS


# ---------------------------------------------------------------------------
# The three families (eigenfunctions.gdshaderinc)
# ---------------------------------------------------------------------------

_SQRT3 = np.sqrt(3.0)


def _sign(value):
    return np.cos(value * np.pi)


def _u(x, y, k, l):
    s1, s2 = _sign((k + l) / 2.0), _sign((k - l) / 2.0)
    return (2.0 * np.sin(k * np.pi * x / _SQRT3) * np.cos(l * np.pi * y)
            - 2.0 * s1 * np.sin(np.pi * x * (k + 3.0 * l) / (2.0 * _SQRT3)) * np.cos(np.pi * y * (k - l) / 2.0)
            - 2.0 * s2 * np.sin(np.pi * x * (k - 3.0 * l) / (2.0 * _SQRT3)) * np.cos(np.pi * y * (k + l) / 2.0))


def _v(x, y, k, l):
    s1, s2 = _sign((k + l) / 2.0), _sign((k - l) / 2.0)
    return (2.0 * np.cos(k * np.pi * x / _SQRT3) * np.cos(l * np.pi * y)
            + 2.0 * s1 * np.cos(np.pi * x * (k + 3.0 * l) / (2.0 * _SQRT3)) * np.cos(np.pi * y * (k - l) / 2.0)
            + 2.0 * s2 * np.cos(np.pi * x * (k - 3.0 * l) / (2.0 * _SQRT3)) * np.cos(np.pi * y * (k + l) / 2.0))


def _u2(x, y, k, l):
    s1, s2 = _sign((k + l) / 2.0), _sign((k - l) / 2.0)
    return (2.0 * np.sin(k * np.pi * x / _SQRT3) * np.sin(l * np.pi * y)
            - 2.0 * s1 * np.sin(np.pi * x * (k + 3.0 * l) / (2.0 * _SQRT3)) * np.sin(np.pi * y * (k - l) / 2.0)
            + 2.0 * s2 * np.sin(np.pi * x * (k - 3.0 * l) / (2.0 * _SQRT3)) * np.sin(np.pi * y * (k + l) / 2.0))


def _v2(x, y, k, l):
    s1, s2 = _sign((k + l) / 2.0), _sign((k - l) / 2.0)
    return (2.0 * np.cos(k * np.pi * x / _SQRT3) * np.sin(l * np.pi * y)
            + 2.0 * s1 * np.cos(np.pi * x * (k + 3.0 * l) / (2.0 * _SQRT3)) * np.sin(np.pi * y * (k - l) / 2.0)
            - 2.0 * s2 * np.cos(np.pi * x * (3.0 * l - k) / (2.0 * _SQRT3)) * np.sin(np.pi * y * (k + l) / 2.0))


def _load_bessel_table() -> np.ndarray:
    """(16 orders, 1024 samples of x in [0, BESSEL_MAX_X]), float32."""
    global _bessel_table
    if _bessel_table is None:
        raw = cv2.imread(str(BESSEL_TABLE_PATH), cv2.IMREAD_UNCHANGED)
        if raw is None:
            raise FileNotFoundError(
                f"{BESSEL_TABLE_PATH} is missing: run tools/sync_assets.py --azoth <checkout>")
        # Single channel, or BGR(A) with the value in every colour channel.
        _bessel_table = (raw if raw.ndim == 2 else raw[..., 2]).astype(np.float64)
    return _bessel_table


def _bessel_lookup(m: int, x):
    """`texture(bessel_texture, vec2(x / max_x, (m + 0.5) / 16)).r` with linear
    filtering and a clamped edge: the row is hit at its centre, so only x
    interpolates."""
    table = _load_bessel_table()
    row = table[min(max(m, 0), table.shape[0] - 1)]
    width = row.shape[0]
    u = np.clip(x / BESSEL_MAX_X, 0.0, 1.0)
    texel = np.clip(u * width - 0.5, 0.0, width - 1)
    lo = np.floor(texel).astype(int)
    hi = np.minimum(lo + 1, width - 1)
    frac = texel - lo
    return row[lo] * (1.0 - frac) + row[hi] * frac


def bessel_j(m: int, x):
    """The shader's besselJ: the table below the blend, the asymptotic form
    above it, smoothstepped between."""
    m = min(max(int(m), 0), 15)
    x = np.asarray(x, dtype=np.float64)
    blend_start = BESSEL_SWITCH - 5.0
    safe = np.maximum(x, 1e-6)
    asym = np.sqrt(2.0 / (np.pi * safe)) * np.cos(safe - (m + 0.5) * np.pi * 0.5) \
        * (1.0 - (4.0 * m * m - 1.0) / (8.0 * safe))
    lookup = _bessel_lookup(m, x)
    t = np.clip((x - blend_start) / (BESSEL_SWITCH - blend_start), 0.0, 1.0)
    t = t * t * (3.0 - 2.0 * t)
    out = np.where(x < blend_start, lookup,
                   np.where(x > BESSEL_SWITCH, asym, lookup * (1.0 - t) + asym * t))
    small = 1.0 if m == 0 else 0.0
    return np.where(x < 0.001, small, out)


def _circle_term(px, py, m: int, n: int, phase: float):
    m = min(max(m, 0), 12)
    n = min(max(n, 1), 8)
    r = np.hypot(px, py)
    radial = bessel_j(m, BESSEL_ZEROS[m, n - 1] * r)
    if m > 0:
        return radial * np.cos(m * np.arctan2(py, px) + phase)
    return radial


def _square_terms(px, py, n, m):
    ux, uy = (px + 1.0) * 0.5, (py + 1.0) * 0.5
    return {
        "dirichlet": np.sin(n * np.pi * ux) * np.sin(m * np.pi * uy),
        "neumann": np.cos(n * np.pi * ux) * np.cos(m * np.pi * uy),
        "mixed_xy": np.sin(n * np.pi * ux) * np.cos(m * np.pi * uy),
        "mixed_yx": np.cos(n * np.pi * ux) * np.sin(m * np.pi * uy),
    }


def _mix(a, b, t):
    return a * (1.0 - t) + b * t


def _organic(t, f1, f2, f3, phase_amp):
    return np.sin(t * f1 + np.sin(t * f2) * phase_amp) * np.cos(t * f3)


def _fwidth(values):
    """|d/dx| + |d/dy| per pixel, GLSL's fwidth."""
    gy, gx = np.gradient(values)
    return np.abs(gx) + np.abs(gy)


# ---------------------------------------------------------------------------
# art_field / art_zone (procedural_art.gdshaderinc)
# ---------------------------------------------------------------------------

def uv_grid(size: int):
    """Pixel-centre UVs of a size x size art quad, (u, v) with v running down,
    as the shader's UV does."""
    c = (np.arange(size, dtype=np.float64) + 0.5) / size
    return np.meshgrid(c, c)


class Field:
    """One look evaluated over one art quad. Everything that does not move with
    time is computed once; `at(t)` is a frame's field."""

    def __init__(self, art: dict, size: int, departure: float, threshold: float):
        self.look = Look(art)
        self.departure = float(departure)
        self.threshold = float(threshold)
        self.u, self.v = uv_grid(size)
        lk = self.look

        qx = (self.u - 0.5) * 2.0
        qy = -((self.v - 0.5) * 2.0)
        c, s = np.cos(lk.rotation), np.sin(lk.rotation)
        warp_room = 1.0 + lk.warp if lk.clip else 1.0
        zoom = warp_room / max(lk.scale, 0.001)
        px = (c * qx - s * qy) * zoom + lk.center[0]
        py = (s * qx + c * qy) * zoom + lk.center[1]
        if lk.warp > 0.0:
            w = lk.warp_seed * 6.2831853
            dx = np.sin(py * 1.7 + w * 1.3) + 0.5 * np.sin(px * 2.3 + py * 0.9 + w * 2.1)
            dy = np.sin(px * 1.9 + w * 0.7) + 0.5 * np.sin(py * 2.1 - px * 1.1 + w * 3.3)
            px, py = px + lk.warp * dx, py + lk.warp * dy

        if lk.family == TRIANGLE:
            edge = np.minimum(py, (1.7320508 * (1.0 - np.abs(px)) - py) * 0.5)
        elif lk.family == CIRCLE:
            edge = 1.0 - np.hypot(px, py)
        else:
            edge = 1.0 - np.maximum(np.abs(px), np.abs(py))
        self.inside = edge > 0.0
        edge_per_px = np.maximum(_fwidth(edge), 1e-6)
        self.edge_fade = np.clip(edge / (EDGE_PX * edge_per_px), 0.0, 1.0)

        # Each term's value over the quad, before its (time-varying) amplitude.
        self.values = []
        for term in lk.terms:
            k, l = term["k"], term["l"]
            if lk.family == TRIANGLE:
                # Transposed: see art_field's note on the waves' own triangle.
                tx, ty = py, px
                ev = term["is_even"]
                dirichlet = _mix(_u(tx, ty, k, l), _v(tx, ty, k, l), ev)
                neumann = _mix(_u2(tx, ty, k, l), _v2(tx, ty, k, l), ev)
                value = _mix(neumann, dirichlet, term["is_dirichlet"])
            elif lk.family == CIRCLE:
                order_peak = 1.0 if k < 0.5 else 0.675 * k ** (-1.0 / 3.0)
                value = _circle_term(px, py, int(k), max(int(l), 1), term["phase"] * np.pi) / order_peak
            else:
                sq = _square_terms(px, py, k, l)
                ev = term["is_even"]
                dirichlet = _mix(sq["dirichlet"], sq["mixed_yx"], ev)
                neumann = _mix(sq["neumann"], sq["mixed_xy"], ev)
                value = _mix(neumann, dirichlet, term["is_dirichlet"])
            self.values.append(value)

    def at(self, t: float) -> np.ndarray:
        lk = self.look
        total = np.zeros_like(self.u)
        for i, (term, value) in enumerate(zip(lk.terms, self.values)):
            amplitude = term["amplitude"] + self.departure * lk.peak * _organic(
                t + i * 17.0, _WOBBLE_F1[i], _WOBBLE_F2[i], _WOBBLE_F3[i], 1.4)
            total = total + amplitude * value
        z = total / max(lk.norm, 1e-6)
        field = z * self.threshold / max(lk.gap, 0.001)
        if lk.clip:
            field = np.where(self.inside, field * self.edge_fade, 0.0)
        return field

    def zone(self, field: np.ndarray) -> np.ndarray:
        """art_zone as the .exr's alpha does it: 1.0 secondary, 0.5 primary, so
        eigenfunction_art's `zone > 0.75` reads both kinds of art the same."""
        lk = self.look
        if lk.fill == 0:
            secondary = np.zeros(field.shape, dtype=bool)
        elif lk.fill == 1:
            secondary = np.ones(field.shape, dtype=bool)
        elif lk.fill >= 4:
            inside = np.hypot((self.u - 0.5) * 2.0, (self.v - 0.5) * 2.0) < lk.zone_radius
            secondary = inside == (lk.fill == 4)
        else:
            secondary = (field > 0.0) == (lk.fill == 2)
        return np.where(secondary, 1.0, 0.5).astype(np.float32)
