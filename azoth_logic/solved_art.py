"""Solved art: a piece's art drawn from its outline's own vibration modes.

A port of the game's solved art (azoth repo, docs/SOLVED_ART_PLAN.md):
`image_data.art` version 2 names an outline, the modes mixed into its base,
its wobble partners, gap and colour zones:

    "art": {"version": 2, "outline": {...}, "roundness": 0.25, "grid": 64,
            "modes": 30, "base": [{"mode": 6, "weight": 1.0}, ...],
            "partners": [5, 7], "gap": 0.2, "fill": {"base": "sign"}}

The modes are solved here, as every machine does in the game (nothing big is
stored): the outline's pixel mask (`ArtOutline.distance`, already ported in
procedural_art), the five-point Laplacian on it with the edge clamped, its
lowest modes. The game finds them with Lanczos (`OutlineModes`); this port
solves the same matrix densely with numpy, which gives the same modes up to
rounding. A mirrored mask is solved as two halves (axis free for the mirrored
modes, clamped for the flipped), a tied cluster of one kind is turned to its
fixed form, every mode is scaled so its peak is 1, signed so the first pixel
at that peak is positive, and rounded to half precision, all as the game does,
so a look's mode numbers name the same patterns here (half precision as Godot
makes it, `godot_half`).

Then `SolvedField` is `art_field` / `art_zone`'s solved branch
(procedural_art.gdshaderinc, `art_solved_waves`) with what `ArtVisuals`
binds for it: framed on its ink (`solved_frame`), the base divided by its peak,
two partners wobbling it by `departure`, the outline's own edge. It has the
same interface as `procedural_art.Field`, so eigenfunction_art draws both.

Parity with the game is pinned by tests/test_solved_art.py against
tests/fixtures/solved_art_reference.json (tools/SolvedArtReference.tscn in the
azoth repo).
"""
from __future__ import annotations

import json
from functools import lru_cache

import numpy as np

from azoth_logic import procedural_art as pa

# ArtVisuals.SOLVED_VERSION; ModeSet's defaults and tiers; OutlineModes' ties.
SOLVED_VERSION = 2
SOLVER_VERSION = 2
DEFAULT_GRID = 64
DEFAULT_TIER = 30
TIER_STEP = 10
TIE_EXACT = 1e-8
PROBE_TIE = 1e-7
EVEN_AXIS = 3.0
ODD_AXIS = 5.0
# ArtVisuals.SOLVED_FRAME_PAD; the shader's edge fade (art_field).
FRAME_PAD = 0.04
EDGE_FADE = 0.015
QUAD_FADE = 0.02
# ModeMix.PARTNERS.
PARTNERS = 2
SOLVE_CACHE = 64

SILHOUETTE_PIECES = {
    "circle": [{"shape": "circle", "at": [0, 0], "size": [0.95, 0.95]}],
    "square": [{"shape": "box", "at": [0, 0], "size": [0.95, 0.95]}],
    "heart": [{"shape": "circle", "at": [-0.42, 0.3], "size": [0.5, 0.5]},
              {"shape": "circle", "at": [0.42, 0.3], "size": [0.5, 0.5]},
              {"shape": "triangle", "at": [0, -0.25], "size": [0.9, 0.7], "turn": float(np.pi)}],
    "pentagon": [{"shape": "pentagon", "at": [0, 0], "size": [1.0, 1.0]}],
    "hexagon": [{"shape": "hexagon", "at": [0, 0], "size": [1.0, 1.0]}],
    "triangle": [{"shape": "triangle", "at": [0, 0], "size": [1.0, 1.0]}],
}


# ---------------------------------------------------------------------------
# Reading a look (ArtVisuals.is_solved / resolve_solved)
# ---------------------------------------------------------------------------

def is_solved(art) -> bool:
    try:
        return isinstance(art, dict) and int(art.get("version", pa.VERSION)) == SOLVED_VERSION
    except (TypeError, ValueError):
        return False


def tier_for(count: int) -> int:
    """ModeSet.tier_for: the look's mode count rounded up to a tier."""
    return max(TIER_STEP, -(-int(count) // TIER_STEP) * TIER_STEP)


def of_silhouette(silhouette: str) -> dict:
    """ArtOutline.of_silhouette: a silhouette as a one-piece outline."""
    pieces = SILHOUETTE_PIECES.get(silhouette, SILHOUETTE_PIECES["triangle"])
    return pa.read_outline({"mirror": True, "blend": pa.OUTLINE_DEFAULT_BLEND, "pieces": pieces})


def resolve_solved(art: dict) -> dict:
    """ArtVisuals.resolve_solved: the look over its defaults, its outline read
    (or its silhouette's, a circle with neither), its tier rounded up."""
    out = {"roundness": pa.DEFAULT_ROUNDNESS, "grid": DEFAULT_GRID, "modes": DEFAULT_TIER,
           "base": [{"mode": 1, "weight": 1.0}], "gap": pa.DEFAULT_GAP, "fill": {"base": "sign"}}
    out.update(art)
    outline = pa.read_outline(out.get("outline"))
    if not any(not p["cut"] for p in outline.get("pieces", [])):
        silhouette = str(out.get("silhouette", "circle"))
        outline = of_silhouette(silhouette if silhouette in pa.SILHOUETTES else "circle")
    out["outline"] = outline
    out["roundness"] = min(max(float(out.get("roundness", pa.DEFAULT_ROUNDNESS)), 0.0), pa.ROUNDNESS_MAX)
    out["grid"] = max(8, int(out.get("grid", DEFAULT_GRID)))
    out["modes"] = tier_for(int(out.get("modes", DEFAULT_TIER)))
    return out


def read_base(base) -> list:
    """ModeMix.read_base: [[mode, weight], ...]."""
    out = []
    for entry in base if isinstance(base, list) else []:
        if isinstance(entry, dict) and "mode" in entry:
            out.append([int(entry["mode"]), float(entry.get("weight", 1.0))])
    return out


def lead_of(base: list) -> int:
    """ModeMix.lead_of: the entry with the biggest weight (the first of equals)."""
    lead, strongest = 0, -1.0
    for mode, weight in base:
        if abs(weight) > strongest:
            strongest, lead = abs(weight), mode
    return lead


# ---------------------------------------------------------------------------
# The solve (OutlineModes, ModeSet)
# ---------------------------------------------------------------------------

def mask_of_outline(outline: dict, size: int, roundness: float) -> np.ndarray:
    """OutlineModes.mask_of_outline: which pixels are inside, rows top to bottom;
    a mirrored outline's right half copied from its left."""
    centres = (np.arange(size) + 0.5) * 2.0 / size - 1.0
    xs, ys = np.meshgrid(centres, -centres)
    inside = pa.outline_distance(outline, xs, ys, roundness) < 0.0
    if outline.get("mirror", True):
        half = (size + 1) // 2
        inside[:, size - half:] = inside[:, :half][:, ::-1]
    return inside.astype(np.uint8)


def _laplacian(mask: np.ndarray, axis_diagonal: float | None):
    """The five-point Laplacian on the inside pixels (row by row), dense; a
    pixel outside counts as 0. With `axis_diagonal`, the mask is a left half
    and a pixel in its last column takes that diagonal."""
    rows, cols = mask.shape
    pixels = np.flatnonzero(mask.ravel())
    index = -np.ones(rows * cols, dtype=np.int64)
    index[pixels] = np.arange(pixels.size)
    a = np.zeros((pixels.size, pixels.size))
    for i, p in enumerate(pixels):
        r, c = divmod(int(p), cols)
        a[i, i] = axis_diagonal if axis_diagonal is not None and c == cols - 1 else 4.0
        for rr, cc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
            if 0 <= rr < rows and 0 <= cc < cols and index[rr * cols + cc] >= 0:
                a[i, index[rr * cols + cc]] = -1.0
    return a, pixels


def _is_mirrored(mask: np.ndarray) -> bool:
    return mask.shape[1] % 2 == 0 and bool(np.array_equal(mask, mask[:, ::-1]))


def solve_mask(mask: np.ndarray, count: int) -> dict:
    """OutlineModes.solve: {values, modes (size*size float32 each), parities, mirrored}."""
    size = mask.shape[0]
    spacing = 2.0 / size
    raws, values, parities = [], [], []
    if _is_mirrored(mask):
        half = size // 2
        half_mask = mask[:, :half]
        merged = []
        solved = []
        for parity, diagonal in ((0, EVEN_AXIS), (1, ODD_AXIS)):
            a, pixels = _laplacian(half_mask, diagonal)
            if pixels.size == 0:
                return {"values": [], "modes": [], "parities": [], "mirrored": True}
            w, v = np.linalg.eigh(a)
            solved.append((w, v, pixels))
            for j in range(w.size):
                merged.append((float(w[j]), parity, j))
        # By eigenvalue; a mirrored and a flipped mode that tie go mirrored first.
        merged.sort(key=_tie_key(merged))
        for value, parity, j in merged[:count]:
            w, v, pixels = solved[parity]
            grid = np.zeros(size * size)
            rows, cols = np.divmod(pixels, half)
            grid[rows * size + cols] = v[:, j]
            grid[rows * size + size - 1 - cols] = v[:, j] * (1.0 if parity == 0 else -1.0)
            raws.append(grid)
            values.append(value / spacing ** 2)
            parities.append(1 if parity == 0 else -1)
        mirrored = True
    else:
        a, pixels = _laplacian(mask, None)
        if pixels.size == 0:
            return {"values": [], "modes": [], "parities": [], "mirrored": False}
        w, v = np.linalg.eigh(a)
        for j in range(min(count, w.size)):
            grid = np.zeros(size * size)
            grid[pixels] = v[:, j]
            raws.append(grid)
            values.append(float(w[j]) / spacing ** 2)
            parities.append(0)
        mirrored = False
    modes = _finish_all(raws, values, parities)
    return {"values": values, "modes": modes, "parities": parities, "mirrored": mirrored}


def _tie_key(merged):
    """Sort key: eigenvalue, with values within TIE_EXACT of a smaller one
    snapped to it, then parity (mirrored first)."""
    ordered = sorted(merged, key=lambda e: e[0])
    snapped = {}
    anchor = None
    for value, parity, j in ordered:
        if anchor is None or abs(value - anchor) > TIE_EXACT * max(abs(value), abs(anchor)):
            anchor = value
        snapped[(parity, j)] = anchor
    return lambda e: (snapped[(e[1], e[2])], e[1], e[0])


def _finish_all(raws, values, parities):
    """OutlineModes._finish_all: tied clusters of one kind to their fixed form,
    then each mode scaled, signed and rounded to half precision."""
    done = set()
    for i in range(len(raws)):
        if i in done:
            continue
        cluster = [i] + [j for j in range(i + 1, len(raws))
                         if parities[j] == parities[i] and abs(values[j] - values[i]) <= TIE_EXACT * abs(values[i])]
        if len(cluster) < 2:
            continue
        turned = _fixed_form([raws[k] for k in cluster])
        for c, k in enumerate(cluster):
            raws[k] = turned[c]
            done.add(k)
    return [_finish(raw) for raw in raws]


def _orthonormal(vectors, keep=-1):
    found = []
    for v in vectors:
        w = np.array(v, dtype=np.float64)
        before = np.sqrt(w @ w)
        for _ in range(2):
            for _, q in found:
                w = w - (w @ q) * q
        left = np.sqrt(w @ w)
        if before > 0.0 and left > 1e-6 * before:
            found.append((left / before, w / left))
    if 0 <= keep < len(found):
        ranked = sorted(range(len(found)), key=lambda i: -found[i][0])[:keep]
        found = [found[i] for i in sorted(ranked)]
    return [q for _, q in found]


def _fixed_form(vectors):
    """OutlineModes._fixed_form: the space's response to the pixel where it is
    strongest, then the same within what is left."""
    basis = _orthonormal(vectors)
    out = []
    while basis:
        strength = np.sum(np.stack(basis) ** 2, axis=0)
        strongest = strength.max()
        probe = int(np.flatnonzero(strength >= strongest * (1.0 - PROBE_TIE))[0])
        f = np.zeros_like(basis[0])
        for q in basis:
            f = f + q * q[probe]
        f = f / np.sqrt(f @ f)
        out.append(f)
        rest = [q - (q @ f) * f for q in basis]
        basis = _orthonormal(rest, len(basis) - 1)
    return out


def _finish(grid: np.ndarray) -> np.ndarray:
    """Peak 1, the first pixel (row order) at the peak positive, half precision."""
    biggest = np.abs(grid).max() if grid.size else 0.0
    if biggest <= 0.0:
        return np.zeros(grid.size, dtype=np.float32)
    first = int(np.flatnonzero(np.abs(grid) >= biggest * (1.0 - 1e-9))[0])
    scale = (1.0 if grid[first] > 0.0 else -1.0) / biggest
    return godot_half(grid * scale)


def godot_half(values) -> np.ndarray:
    """`values` through float32 to half precision and back, exactly as Godot's
    Image.convert(FORMAT_RH) does it (Math::make_half_float): the mantissa
    truncated, not rounded, and anything below half's normal range (2^-14)
    flushed to zero. numpy's float16 rounds to nearest, which differed from the
    game's ModeSet in about one value in six."""
    bits = np.asarray(values, dtype=np.float32).view(np.uint32)
    exponent = bits & np.uint32(0x7F800000)
    kept = np.where(exponent <= np.uint32(0x38000000), bits & np.uint32(0x80000000), bits & np.uint32(0xFFFFE000))
    return kept.astype(np.uint32).view(np.float32)


@lru_cache(maxsize=SOLVE_CACHE)
def _solved(outline_json: str, roundness: float, grid: int, tier: int) -> dict:
    outline = json.loads(outline_json)
    return solve_mask(mask_of_outline(outline, grid, roundness), tier)


def mode_set(look: dict) -> dict:
    """The look's outline solved (a resolved look), cached per outline."""
    return _solved(json.dumps(look["outline"], sort_keys=True), float(look["roundness"]),
                   int(look["grid"]), int(look["modes"]))


# ---------------------------------------------------------------------------
# What ArtVisuals binds (ModeMix, solved_frame)
# ---------------------------------------------------------------------------

def same_kind(parities, lead: int, mode: int) -> bool:
    a, b = parities[lead - 1], parities[mode - 1]
    return a == 0 or b == 0 or a == b


def default_partners(solved: dict, base: list, how_many: int = PARTNERS) -> list:
    """ModeMix.default_partners: nearest the lead in eigenvalue, of its kind,
    not in the base, the lower number first on a tie."""
    lead = lead_of(base)
    if lead < 1:
        return []
    values, parities = solved["values"], solved["parities"]
    taken = {m for m, _ in base}
    choices = [m for m in range(1, len(values) + 1) if m not in taken and same_kind(parities, lead, m)]
    choices.sort(key=lambda m: (abs(values[m - 1] - values[lead - 1]), m))
    return choices[:how_many]


def base_field(solved: dict, base: list) -> np.ndarray:
    """ModeMix.field: the base summed on the solve's grid (float32, as in the game)."""
    modes = solved["modes"]
    total = np.zeros_like(modes[0]) if modes else np.zeros(0, dtype=np.float32)
    for mode, weight in base:
        if 1 <= mode <= len(modes):
            total = (total + np.float32(weight) * modes[mode - 1]).astype(np.float32)
    return total


def solved_frame(field: np.ndarray, peak: float, grid: int, gap: float):
    """ArtVisuals.solved_frame: the square holding the ink (|field| >= gap * peak),
    padded, as (x, y, half-width); the whole quad with none."""
    if peak <= 0.0:
        return (0.0, 0.0, 1.0)
    inked = np.abs(field.reshape(grid, grid)) >= gap * peak
    if not inked.any():
        return (0.0, 0.0, 1.0)
    rows, cols = np.nonzero(inked)
    xs = (cols + 0.5) * 2.0 / grid - 1.0
    ys = 1.0 - (rows + 0.5) * 2.0 / grid
    pixel = 1.0 / grid
    low = (xs.min() - pixel, ys.min() - pixel)
    high = (xs.max() + pixel, ys.max() + pixel)
    half = min(max(high[0] - low[0], high[1] - low[1]) * 0.5 * (1.0 + FRAME_PAD), 1.0)
    cx = min(max((low[0] + high[0]) * 0.5, -1.0 + half), 1.0 - half)
    cy = min(max((low[1] + high[1]) * 0.5, -1.0 + half), 1.0 - half)
    return (cx, cy, half)


def sample(values: np.ndarray, grid: int, px, py):
    """ModeMix.sample: the grid at (px, py), bilinear between pixel centres."""
    image = values.reshape(grid, grid)
    gx = (np.asarray(px) + 1.0) * 0.5 * grid - 0.5
    gy = (1.0 - np.asarray(py)) * 0.5 * grid - 0.5
    x0 = np.clip(np.floor(gx).astype(int), 0, grid - 1)
    y0 = np.clip(np.floor(gy).astype(int), 0, grid - 1)
    x1 = np.minimum(x0 + 1, grid - 1)
    y1 = np.minimum(y0 + 1, grid - 1)
    fx = np.clip(gx - x0, 0.0, 1.0)
    fy = np.clip(gy - y0, 0.0, 1.0)
    top = image[y0, x0] + (image[y0, x1] - image[y0, x0]) * fx
    bottom = image[y1, x0] + (image[y1, x1] - image[y1, x0]) * fx
    return top + (bottom - top) * fy


# ---------------------------------------------------------------------------
# art_field / art_zone, solved (procedural_art.gdshaderinc)
# ---------------------------------------------------------------------------

class SolvedField:
    """One solved look over one art quad, with procedural_art.Field's interface:
    `at(t)` a frame's field, `zone(field)` its colour zones."""

    def __init__(self, art: dict, size: int, departure: float, threshold: float, uv=None, placement=None):
        look = resolve_solved(art)
        self.look = look
        self.departure = float(departure)
        self.threshold = float(threshold)
        self.gap = float(look.get("gap", pa.DEFAULT_GAP))
        self.fill = pa.fill_index(look)
        self.u, self.v = uv if uv is not None else pa.uv_grid(size)
        solved = mode_set(look)
        self.solved = solved
        grid = int(look["grid"])
        count = len(solved["modes"])
        base = [[m, w] for m, w in read_base(look.get("base")) if 1 <= m <= count]
        if not base and count:
            base = [[1, 1.0]]
        base = base[:4]
        partners = look.get("partners")
        if isinstance(partners, list):
            partners = [int(m) for m in partners if 1 <= int(m) <= count]
        else:
            partners = default_partners(solved, base)
        self.partners = partners[:PARTNERS]
        field = base_field(solved, base) if count else np.zeros(grid * grid, dtype=np.float32)
        self.peak = max(float(np.abs(field).max()) if field.size else 0.0, 1e-6)
        self.frame = solved_frame(field, self.peak, grid, self.gap)
        qx = (self.u - 0.5) * 2.0
        qy = -((self.v - 0.5) * 2.0)
        fx, fy, fz = self.frame
        self.px, self.py = fx + qx * fz, fy + qy * fz
        outline, roundness = look["outline"], float(look["roundness"])
        self.edge = pa._smoothstep(0.0, EDGE_FADE, -pa.outline_distance(outline, self.px, self.py, roundness)) \
            * pa._smoothstep(0.0, QUAD_FADE, 1.0 - np.maximum(np.abs(self.px), np.abs(self.py)))
        self.base_values = np.zeros_like(self.u)
        for mode, weight in base:
            self.base_values = self.base_values + weight * sample(solved["modes"][mode - 1], grid, self.px, self.py)
        self.base_values = self.base_values / self.peak
        self.partner_values = [sample(solved["modes"][m - 1], grid, self.px, self.py) for m in self.partners]

    def at(self, t: float) -> np.ndarray:
        total = self.base_values
        for j, values in enumerate(self.partner_values):
            w = self.departure * pa._organic(t + (j + 1) * 17.0, pa._WOBBLE_F1[j + 1], pa._WOBBLE_F2[j + 1],
                                             pa._WOBBLE_F3[j + 1], 1.4)
            total = total + w * values
        return np.where(self.edge > 0.0, total * self.edge * self.threshold / max(self.gap, 0.001), 0.0)

    def zone(self, field: np.ndarray) -> np.ndarray:
        """art_zone: 1.0 secondary, 0.5 primary, as an .exr's alpha."""
        if self.fill == 0:
            secondary = np.zeros(field.shape, dtype=bool)
        elif self.fill == 1:
            secondary = np.ones(field.shape, dtype=bool)
        elif self.fill >= 6:
            secondary = pa.outline_zone(self.look["outline"], self.px, self.py,
                                        float(self.look["roundness"])) == (self.fill == 6)
        elif self.fill >= 4:
            inside = np.hypot((self.u - 0.5) * 2.0, (self.v - 0.5) * 2.0) < float(
                self.look.get("fill", {}).get("radius", pa.DEFAULT_ZONE_RADIUS))
            secondary = inside == (self.fill == 4)
        else:
            secondary = (field > 0.0) == (self.fill == 2)
        return np.where(secondary, 1.0, 0.5).astype(np.float32)
