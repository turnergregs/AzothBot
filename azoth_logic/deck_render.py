"""Multi-card layouts: a deck grid, a fanned sample hand, and an upgrade comparison.

Both are STATIC. A 110-card deck animating at 60 frames each would be tens of
megabytes and unreadable at thumbnail size, so the animation stays on `/render`,
where one card fills the message.

Art fetching is the bottleneck, not drawing -- measured at 0.68s per card
downloading versus 0.04s rendering. Downloads are therefore parallelised and
deduplicated; a 110-card deck goes from ~79s serially, which exceeds the
command timeout, to roughly ten.
"""
from __future__ import annotations

import hashlib
import io
import math
import random
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageDraw

from azoth_logic import art_cache
from azoth_logic import card_layout as L
from azoth_logic import card_render
from azoth_logic import eigenfunction_art as ef
from azoth_logic import holo
from azoth_logic import fate_layout as F

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

# An upgrade comparison is two or three faces, not a hundred, so they can be
# read rather than recognised -- this is nearly twice the grid width.
COMPARE_CARD_WIDTH = 380
COMPARE_GUTTER = 20
COMPARE_LABEL_BAND = 40
COMPARE_LABEL_SIZE = 24

# A comparison shares one palette across BOTH faces, and they are routinely
# different colour schemes -- an orange card beside a pink aspect. 64 (the
# single-card value) is tuned for one scheme; this is the same budget per side.
# Measured cost of the extra colours: about 15% on the file, against a cap the
# largest comparison uses a twentieth of.
COMPARE_GIF_COLORS = 128

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
    # `sheen=False` everywhere here: this feeds the deck grid, the sample hand
    # and the comparison, and none of them wants the holographic material baked
    # in. A grid draws at 200px, where it is invisible and would cost a 110-card
    # deck 110 applications; the comparison applies it itself, per side, because
    # the upgraded face uses a different intensity.
    if kind == "aspect":
        data, _ = fate_render.render_aspect(item, art, animate=False, sheen=False)
        return Image.open(io.BytesIO(data)).convert("RGBA")
    if kind == "rite":
        data, _ = fate_render.render_rite(item, sheen=False)
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


def _frames_for(item, kind, art, duration: float, fps: int) -> list:
    """Every frame of one face, as RGBA. A single frame when it does not animate.

    The animated branches mirror `card_render.render_gif` and
    `fate_render.render_aspect` exactly -- up to but NOT including `to_gif`,
    which is what makes them composable. Rites are absent on purpose: only
    `cards` has an `upgrades` column, so a comparison is always a card beside a
    card or an aspect.
    """
    from azoth_logic import fate_render

    if kind == "card" and art and card_render.is_animated(item):
        face = card_render._base_face(item)
        primary, secondary = ef.colors_for_card(item)
        with card_render._temp(art, ".exr") as path:
            arts = ef.frames(path, primary, secondary, duration=duration, fps=fps,
                             departure=ef.departure_for_card(item))
        pos = (round(L.ART[0]), round(L.ART[1]))
        frames = []
        for art_frame in arts:
            frame = face.copy()
            frame.alpha_composite(art_frame, pos)
            frames.append(frame)
        return frames

    if kind == "aspect" and art and fate_render.is_animated(item):
        face = fate_render._aspect_face(item)
        pos = (round(F.ASPECT_ART[0]), round(F.ASPECT_ART[1]))
        size = (round(F.ASPECT_ART[2]), round(F.ASPECT_ART[3]))
        base, accent = fate_render.aspect_art_colors(item)
        with card_render._temp(art, ".exr") as path:
            arts = ef.frames(path, base, accent, duration=duration, fps=fps,
                             departure=ef.departure_for_card(item))
        frames = []
        for art_frame in arts:
            frame = face.copy()
            frame.alpha_composite(art_frame.resize(size, Image.LANCZOS), pos)
            frames.append(frame)
        return frames

    return [_still_for(item, kind, art)]


def _animates(item, kind: str, art) -> bool:
    """Whether this face has anything to animate."""
    from azoth_logic import fate_render
    if not art:
        return False
    if kind == "card":
        return card_render.is_animated(item)
    if kind == "aspect":
        return fate_render.is_animated(item)
    return False


def _comparison_key(items, kinds, labels, art, width, duration, fps,
                    holo_levels=None) -> str:
    """One key covering every face, so any edit to either side misses.

    `art_cache.render_key` keys a SINGLE item; a comparison is a function of
    both, plus the captions, which change with the tier and the upgraded kind.
    """
    parts = [art_cache.render_key(item, art.get(id(item)), kind,
                                  width=width, duration=duration, fps=fps)
             for item, kind in zip(items, kinds)]
    parts += list(labels) + [f"{level:g}" for level in (holo_levels or [])]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def _comparison_sides(items, kinds, art, width: int, animate: bool,
                      duration: float, fps: int, holo_levels=None) -> list:
    """Each face's frames, cropped and scaled to a common box.

    Cropped to the side's OWN alpha box -- computed across all of its frames at
    once, so the crop cannot jitter mid-animation.

    Cropping matters more here than in a grid. A card face carries ~63px of
    empty canvas above and below (`render_png` crops it for exactly this
    reason); an aspect face does not, because `render_aspect` already cropped.
    Scaling both to one box without cropping first therefore drew the card
    visibly smaller than the aspect beside it.

    EACH SIDE KEEPS ITS OWN ASPECT RATIO. Only the width is fixed; the height
    follows from the crop. Deriving the height from CARD_W x CARD_H instead --
    which is what this did at first -- squeezes the card, because the crop is
    NOT that shape: a 552x766 crop (0.72) forced into a 380x609 box (0.624)
    loses 13% of its width. The whole point of cropping is that the face is no
    longer the full canvas, so the full canvas cannot supply the target shape.
    """
    sides = []
    for index, (item, kind) in enumerate(zip(items, kinds)):
        frames = (_frames_for(item, kind, art.get(id(item)), duration, fps)
                  if animate else [_still_for(item, kind, art.get(id(item)))])
        box = card_render.alpha_bbox(frames)
        cropped = [f.crop(box) for f in frames]
        height = max(1, round(cropped[0].height * width / cropped[0].width))
        frames = [f.resize((width, height), Image.LANCZOS) for f in cropped]

        # After the resize, not before: the sheen is a smooth gradient, so
        # there is nothing to lose by computing it over four times fewer pixels.
        #
        # Per-side INTENSITY, not a yes/no: every card wears the sheen at 0.06,
        # and an upgraded one at 0.15. A flag here would flatten that difference
        # and make the two faces look identically foiled.
        level = (holo_levels or [])[index] if holo_levels else 0.0
        if level:
            frames = holo.apply_all(frames, intensity=level)
        sides.append(frames)
    return sides


def render_comparison(items, kinds, labels, holo_levels=None,
                      card_width: int = COMPARE_CARD_WIDTH,
                      animate: bool = False, duration: float = 4.0, fps: int = 15):
    """Faces side by side under captions. Returns PNG bytes.

    Built for a card and its upgrade, so `items`/`kinds`/`labels` run in
    parallel and `kinds` is REQUIRED -- unlike the deck paths, the whole point
    here is that the two sides may not be the same kind. 28 cards upgrade into
    aspects, and drawing the upgraded face with the card renderer would show a
    card that cannot exist.

    STATIC, like every other multi-face layout in this module. The comparison is
    a reading task -- what changed in the text, the valence, the attunement --
    and animating it would cost seconds per side to make the words harder to
    read. `/render` without `compare` still gives the animated single face.
    """
    if not items:
        raise ValueError("nothing to compare")
    if len(items) != len(kinds) or len(items) != len(labels):
        raise ValueError("items, kinds and labels must be the same length")

    art = fetch_art_many(items, kinds=kinds)
    moving = animate and any(_animates(i, k, art.get(id(i)))
                             for i, k in zip(items, kinds))

    # Cache the GIFs only, matching card_render.render: a still comparison is
    # two cached art fetches and a paste, while an animated one runs the
    # eigenfunction shader twice and takes 3-7s.
    key = None
    if moving:
        key = _comparison_key(items, kinds, labels, art, card_width, duration, fps,
                              holo_levels)
        hit = art_cache.get_render(key, "gif")
        if hit is not None:
            return hit, "gif"

    sides = _comparison_sides(items, kinds, art, card_width, moving, duration, fps,
                              holo_levels)
    cw = card_width
    # A card crop and an aspect crop are different shapes, so the faces can
    # differ in height. The row is as tall as the tallest and the others are
    # centred in it -- stretching either to match would put back exactly the
    # distortion the per-side aspect ratio removed.
    ch = max(side[0].height for side in sides)
    pages = max(len(side) for side in sides)

    # The labels never change, so they are drawn ONCE onto a background that
    # every frame is copied from. GIF frame differencing then has nothing to
    # encode outside the two art boxes.
    board = Image.new(
        "RGBA",
        (len(sides) * cw + COMPARE_GUTTER * (len(sides) + 1),
         ch + COMPARE_GUTTER * 2 + COMPARE_LABEL_BAND),
        GRID_BG + (255,),
    )
    draw = ImageDraw.Draw(board)
    font = card_render._font(COMPARE_LABEL_SIZE)
    for i, label in enumerate(labels):
        x = COMPARE_GUTTER + i * (cw + COMPARE_GUTTER)
        # Centred under its own face, not under the sheet.
        left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
        draw.text(
            (x + (cw - (right - left)) // 2 - left,
             COMPARE_GUTTER + ch + (COMPARE_LABEL_BAND - (bottom - top)) // 2 - top),
            label, font=font, fill=(235, 235, 235))

    def compose(index: int) -> Image.Image:
        sheet = board.copy()
        for i, side in enumerate(sides):
            # A still side holds its single frame while the other one moves.
            face = side[index % len(side)]
            sheet.alpha_composite(face,
                                  (COMPARE_GUTTER + i * (cw + COMPARE_GUTTER),
                                   COMPARE_GUTTER + (ch - face.height) // 2))
        return sheet

    if pages == 1:
        buf = io.BytesIO()
        compose(0).convert("RGB").save(buf, format="PNG", optimize=True)
        return buf.getvalue(), "png"

    data = card_render.to_gif([compose(i) for i in range(pages)],
                              fps=fps, colors=COMPARE_GIF_COLORS)
    if key:
        art_cache.put_render(key, "gif", data)
    return data, "gif"


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
