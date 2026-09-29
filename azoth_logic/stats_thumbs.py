"""The picture beside a one-item /stats report: which thing it is about.

stats_cards is pure (rows in, Card out); this is the I/O half, kept apart so
it stays that way. Cards, aspects and rites are their rendered face, the same
still `/render` and the deck grid draw. Bosses and heroes have no face in the
bot (the hero renderer was retired, docs/CARD_RENDERING.md § Retired), so
they are their art alone, shaded in the item's colour, cut to a circle.

Slow (an art download and an EXR decode): call it off the event loop.
Returns None when there is nothing to draw, and the report draws without one.
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw

from azoth_logic import art_cache, card_render, deck_render
from azoth_logic import eigenfunction_art as ef
from azoth_logic import stats_charts as sc

# In 1x points: the height of the header the thumb sits beside (the title,
# two subtitle lines and a row of tiles).
THUMB_H = 140


def _rgb(colour: str) -> tuple:
    return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))


def _face(row: dict, kind: str) -> Image.Image | None:
    art = deck_render.fetch_art_many([row], kinds=[kind]).get(id(row))
    face = deck_render._still_for(row, kind, art)
    return face.crop(card_render.alpha_bbox([face]))


def _plate(row: dict, colour: str) -> Image.Image | None:
    """A boss's or hero's art, shaded in `colour`, cut to a circle."""
    if ef.needs_download(row):
        try:
            data = art_cache.fetch_art_cached(card_render.EXR_BUCKET if ef.is_exr(row)
                                              else card_render.PNG_BUCKET,
                                              row["image"], card_render.download_art)
        except Exception:
            return None
    else:
        data = None
    art = ef.item_still(row, data, ef.BASE_COLOR, _rgb(colour))
    if art is None:
        return None
    # Cut to a circle, the shape the game frames boss and hero art in, but
    # with no ring drawn round it (Turner's review): the art is the picture.
    mask = Image.new("L", art.size, 0)
    ImageDraw.Draw(mask).ellipse([0, 0, art.width - 1, art.height - 1], fill=255)
    plate = Image.new("RGBA", art.size, (0, 0, 0, 0))
    plate.paste(art, (0, 0), mask)
    return plate


def thumbnail(kind: str, row: dict | None, colour: str) -> Image.Image | None:
    """The item's picture at THUMB_H points tall (drawn at sc.SCALE)."""
    if not row:
        return None
    try:
        img = _plate(row, colour) if kind in ("boss", "hero") else _face(row, kind)
    except Exception:
        return None
    if img is None:
        return None
    height = sc.px(THUMB_H)
    return img.resize((max(1, round(img.width * height / img.height)), height), Image.LANCZOS)
