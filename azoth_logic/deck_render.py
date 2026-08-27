"""Multi-card layouts: a deck grid and a fanned sample hand.

Both are STATIC. A 110-card deck animating at 60 frames each would be tens of
megabytes and unreadable at thumbnail size, so the animation stays on `/render`,
where one card fills the message.

Art fetching is the bottleneck, not drawing -- measured at 0.68s per card
downloading versus 0.04s rendering. Downloads are therefore parallelised and
deduplicated; a 110-card deck goes from ~79s serially, which exceeds the
command timeout, to roughly ten.
"""
from __future__ import annotations

import io
import math
import random
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

from azoth_logic import art_cache
from azoth_logic import card_layout as L
from azoth_logic import card_render

# Enough parallelism to hide network latency without hammering Supabase.
DOWNLOAD_WORKERS = 12

# Cards per row in a deck grid. 10 keeps a 110-card deck to 11 rows and the
# sheet within Discord's 10MB limit at the default card width.
GRID_COLUMNS = 10
GRID_CARD_WIDTH = 200
GRID_GUTTER = 10
GRID_BG = (30, 31, 34)

# A deck bigger than this is refused rather than silently truncated.
MAX_GRID_CARDS = 200

HAND_CARD_WIDTH = 300
HAND_SPREAD_DEGREES = 26

# Horizontal step between cards, as a fraction of card width. A held hand in the
# game overlaps far more than this, but there you hover a card to read it. In a
# Discord screenshot the name and rules text have to survive the overlap, and
# both sit on the LEFT of the card -- so the step has to clear them.
HAND_STEP = 0.80


def fetch_art_many(cards, workers: int = DOWNLOAD_WORKERS, kinds=None) -> dict:
    """Download art for many items at once, keyed by `id(item)`.

    Deduplicated by image filename: decks repeat art, and re-downloading the
    same EXR once per copy is the difference between ten seconds and a minute.
    A card whose art fails to download maps to None and renders without art
    rather than failing the whole sheet -- one bad asset should not cost you the
    deck.

    `kinds` parallels `cards`. Without it everything is treated as a card, which
    is what the deck and hand paths want.
    """
    def key_for(i, item):
        kind = kinds[i] if kinds else "card"
        bucket = _bucket_for(item, kind)
        name = item.get("image")
        return (bucket, name) if bucket and name else None

    keys = [key_for(i, item) for i, item in enumerate(cards)]
    wanted = {k: None for k in keys if k}

    def grab(key):
        bucket, name = key
        try:
            # Cache-aware: a repeat search or a re-render of the same deck skips
            # the network entirely, which is 95% of the cost.
            return key, art_cache.fetch_art_cached(bucket, name, card_render.download_art)
        except Exception:
            return key, None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for key, data in pool.map(grab, list(wanted)):
            wanted[key] = data

    return {id(item): wanted.get(keys[i]) for i, item in enumerate(cards)}


def _bucket_for(item: dict, kind: str = "card") -> str | None:
    """Which Storage bucket holds this item's art, or None when it has no face art.

    `.exr` always lives in `eigenfunctions` regardless of content type; flat art
    lives in the per-type bucket.

    A RITE returns None. `event_card.tscn` ships its Image node hidden -- the
    `image` column feeds the draft thumbnail, not the card face -- so fetching it
    is a download whose result is thrown away. Dispatching on `kind` rather than
    sniffing for an `attunement` key is also what keeps an aspect out of the
    cards bucket in a mixed `/search` result.
    """
    if kind == "rite":
        return None
    if card_render.is_animated(item):
        return card_render.EXR_BUCKET
    if kind == "aspect":
        return "aspectimages"
    return card_render.PNG_BUCKET


def _still_for(item, kind, art):
    """One static face, dispatched by content type."""
    from azoth_logic import fate_render
    if kind == "aspect":
        data, _ = fate_render.render_aspect(item, art, animate=False)
        return Image.open(io.BytesIO(data)).convert("RGBA")
    if kind == "rite":
        data, _ = fate_render.render_rite(item)
        return Image.open(io.BytesIO(data)).convert("RGBA")
    return card_render.render_still(item, art)


def _faces(cards, width: int, kinds=None):
    """Render each item once, scaled to `width`.

    `kinds` parallels `cards`; without it everything is treated as a card, which
    is what the deck and hand paths want.
    """
    art = fetch_art_many(cards, kinds=kinds)
    scale = width / L.CARD_W
    size = (width, round(L.CARD_H * scale))
    out = []
    for i, card in enumerate(cards):
        kind = kinds[i] if kinds else "card"
        face = _still_for(card, kind, art.get(id(card)))
        out.append(face.resize(size, Image.LANCZOS))
    return out


def render_grid(cards, columns: int = GRID_COLUMNS, card_width: int = GRID_CARD_WIDTH,
                kinds=None) -> bytes:
    """Items tiled into a sheet. Returns PNG bytes.

    `kinds` parallels `cards` and lets a mixed result set (search) render cards,
    aspects and rites together; omit it for a deck, which is cards only.
    """
    if not cards:
        raise ValueError("no cards to render")
    if len(cards) > MAX_GRID_CARDS:
        raise ValueError(
            f"{len(cards)} cards is past the {MAX_GRID_CARDS}-card limit for a grid; "
            f"the sheet would exceed Discord's upload cap")

    faces = _faces(cards, card_width, kinds)
    cw, ch = faces[0].size
    rows = math.ceil(len(faces) / columns)
    sheet = Image.new(
        "RGB",
        (columns * cw + GRID_GUTTER * (columns + 1),
         rows * ch + GRID_GUTTER * (rows + 1)),
        GRID_BG,
    )
    for i, face in enumerate(faces):
        x = GRID_GUTTER + (i % columns) * (cw + GRID_GUTTER)
        y = GRID_GUTTER + (i // columns) * (ch + GRID_GUTTER)
        sheet.paste(_flatten(face), (x, y))

    buf = io.BytesIO()
    sheet.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_hand(cards, hand_size: int = 6, seed=None,
                card_width: int = HAND_CARD_WIDTH,
                spread: float = HAND_SPREAD_DEGREES) -> bytes:
    """A random sample hand, fanned. Returns PNG bytes.

    `seed` makes the draw reproducible, which is what lets the tests assert on
    it -- without it the same deck gives a different hand every call.
    """
    if not cards:
        raise ValueError("no cards to render")
    rng = random.Random(seed)
    hand_size = max(1, min(hand_size, len(cards)))
    drawn = rng.sample(list(cards), hand_size)

    faces = _faces(drawn, card_width)
    cw, ch = faces[0].size

    # Fan about a pivot BELOW the cards so they splay from a common point, the
    # way a held hand does. Rotating each card about its own centre instead
    # reads as scattered rather than fanned.
    pivot_y = int(ch * 1.35)
    step_deg = spread / max(1, hand_size - 1) if hand_size > 1 else 0.0
    start_deg = -spread / 2 if hand_size > 1 else 0.0

    # Rotating a card-plus-pivot block expands its bounding box; size the canvas
    # from the widest rotation so nothing clips at the edges of the fan.
    max_angle = math.radians(max(abs(start_deg), abs(start_deg + step_deg * (hand_size - 1))))
    rot_w = cw * math.cos(max_angle) + pivot_y * math.sin(max_angle)
    rot_h = cw * math.sin(max_angle) + pivot_y * math.cos(max_angle)

    span = cw * HAND_STEP * (hand_size - 1)
    canvas = Image.new("RGBA",
                       (int(span + rot_w * 2), int(rot_h + ch * 0.25)),
                       (0, 0, 0, 0))
    origin_x = canvas.width // 2

    for i, face in enumerate(faces):
        angle = start_deg + step_deg * i
        block = Image.new("RGBA", (cw, pivot_y), (0, 0, 0, 0))
        block.alpha_composite(face, (0, 0))
        rotated = block.rotate(-angle, resample=Image.BICUBIC, expand=True)
        offset = (i - (hand_size - 1) / 2) * cw * HAND_STEP
        canvas.alpha_composite(
            rotated,
            (int(origin_x + offset - rotated.width / 2), int(ch * 0.08)),
        )

    bbox = canvas.getbbox()
    if bbox:
        canvas = canvas.crop(bbox)
    buf = io.BytesIO()
    _flatten(canvas).save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _flatten(img: Image.Image) -> Image.Image:
    """Composite onto Discord's dark background.

    Sheets are PNG so alpha would survive, but a transparent card edge reads as a
    hole against a light theme; the single-card path flattens for the same
    reason.
    """
    if img.mode != "RGBA":
        return img.convert("RGB")
    bg = Image.new("RGBA", img.size, card_render.DISCORD_BG + (255,))
    return Image.alpha_composite(bg, img).convert("RGB")
