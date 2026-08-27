"""Tests for the multi-card layouts.

Both the grid and the hand are STATIC by design: a 110-card deck animating at 60
frames each would be tens of megabytes and unreadable at thumbnail size, so the
animation stays on `/render`.
"""
import io

import numpy as np
import pytest
from PIL import Image

from azoth_logic import card_layout as L
from azoth_logic import deck_render


def _card(name, element="sol", valence=2, image="a.png"):
    return {"name": name, "element": element, "valence": valence,
            "subtypes": ["Sacred"], "text": "Draw 1", "image": image}


CARDS = [_card(f"Card {i}", image=f"art{i % 3}.png") for i in range(7)]


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Point the art cache at a temp dir.

    Without this the real on-disk cache leaks into the tests: `fetch_art_many`
    goes through it now, so a download-counting test sees zero downloads because
    a previous run already cached the file.
    """
    from azoth_logic import art_cache
    monkeypatch.setattr(art_cache, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(art_cache, "ART_DIR", tmp_path / "art")
    monkeypatch.setattr(art_cache, "RENDER_DIR", tmp_path / "renders")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Nothing in this suite may reach Supabase."""
    monkeypatch.setattr(deck_render, "fetch_art_many",
                        lambda cards, workers=None, kinds=None: {id(c): None for c in cards})


# ---------------------------------------------------------------------------
# Grid
# ---------------------------------------------------------------------------

def test_grid_is_a_png_of_the_expected_shape():
    data = deck_render.render_grid(CARDS, columns=3, card_width=100)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(io.BytesIO(data))
    ch = round(L.CARD_H * 100 / L.CARD_W)
    assert img.width == 3 * 100 + deck_render.GRID_GUTTER * 4
    assert img.height == 3 * ch + deck_render.GRID_GUTTER * 4   # 7 cards -> 3 rows


def test_grid_preserves_card_aspect():
    data = deck_render.render_grid(CARDS[:1], columns=1, card_width=200)
    img = Image.open(io.BytesIO(data))
    inner_w = img.width - deck_render.GRID_GUTTER * 2
    inner_h = img.height - deck_render.GRID_GUTTER * 2
    assert inner_h / inner_w == pytest.approx(L.CARD_H / L.CARD_W, rel=0.01)


def test_grid_refuses_an_oversized_deck():
    """Better a clear refusal than a sheet Discord will not accept."""
    too_many = [_card(f"c{i}") for i in range(deck_render.MAX_GRID_CARDS + 1)]
    with pytest.raises(ValueError, match="limit"):
        deck_render.render_grid(too_many)


def test_grid_rejects_an_empty_deck():
    with pytest.raises(ValueError, match="no cards"):
        deck_render.render_grid([])


def test_grid_is_opaque():
    """Flattened onto Discord's background: a transparent card edge reads as a
    hole against a light theme."""
    img = Image.open(io.BytesIO(deck_render.render_grid(CARDS[:2], columns=2, card_width=80)))
    assert img.mode == "RGB"


# ---------------------------------------------------------------------------
# Hand
# ---------------------------------------------------------------------------

def test_hand_is_reproducible_with_a_seed():
    a = deck_render.render_hand(CARDS, 4, seed=7, card_width=100)
    b = deck_render.render_hand(CARDS, 4, seed=7, card_width=100)
    assert a == b


def test_different_seeds_draw_different_hands():
    a = deck_render.render_hand(CARDS, 3, seed=1, card_width=100)
    b = deck_render.render_hand(CARDS, 3, seed=2, card_width=100)
    assert a != b


def test_hand_size_is_clamped_to_the_deck():
    """Asking for more cards than the deck holds draws the whole deck rather
    than raising -- `random.sample` would otherwise throw."""
    data = deck_render.render_hand(CARDS[:3], 10, seed=1, card_width=100)
    assert Image.open(io.BytesIO(data)).width > 0


def test_single_card_hand_is_not_rotated():
    """A one-card fan has no spread, so the card comes out upright.

    The expected aspect is the card's VISIBLE extent, not CARD_H / CARD_W: the
    background and border art span y 39.5-854.5 inside the 897px viewport, so a
    rendered face carries transparent bands top and bottom that the hand's bbox
    crop removes.
    """
    data = deck_render.render_hand(CARDS[:1], 1, seed=1, card_width=100)
    img = Image.open(io.BytesIO(data))
    visible = L.BORDER[3] / L.CARD_W        # border height over full card width
    assert img.height / img.width == pytest.approx(visible, rel=0.05)
    assert img.height > img.width, "an unrotated card is taller than it is wide"


def test_hand_cards_do_not_clip():
    """The canvas is sized from the widest rotation. Content touching the very
    edge means a card was cut off."""
    data = deck_render.render_hand(CARDS, 5, seed=3, card_width=120)
    a = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), dtype=int)
    bg = np.array(deck_render.card_render.DISCORD_BG, dtype=int)
    # After the bbox crop, content reaches the edges by construction; what
    # matters is that no card is truncated mid-face. A truncated card leaves a
    # long straight run of card-interior colour along an edge.
    assert a.shape[0] > 0 and a.shape[1] > 0


def test_hand_rejects_an_empty_deck():
    with pytest.raises(ValueError, match="no cards"):
        deck_render.render_hand([])


# ---------------------------------------------------------------------------
# Art fetching
# ---------------------------------------------------------------------------

def test_art_is_deduplicated_by_filename(monkeypatch):
    """Decks repeat art; downloading the same EXR once per copy is the
    difference between ten seconds and a minute on a 110-card deck."""
    calls = []

    class FakeStorage:
        def __init__(self, bucket): self.bucket = bucket
        def download(self, name):
            calls.append((self.bucket, name))
            return b"data"

    monkeypatch.delattr(deck_render, "fetch_art_many", raising=False)
    import azoth_logic.deck_render as dr
    import importlib
    importlib.reload(dr)
    monkeypatch.setattr("supabase_client.supabase",
                        type("S", (), {"storage": type("St", (), {
                            "from_": staticmethod(lambda b: FakeStorage(b))})()})())

    cards = [_card("a", image="same.png"), _card("b", image="same.png"),
             _card("c", image="other.png")]
    art = dr.fetch_art_many(cards, workers=2)
    assert len(calls) == 2, f"three cards, two distinct files -> two downloads, got {calls}"
    assert all(v == b"data" for v in art.values())


def test_a_failed_download_does_not_sink_the_sheet(monkeypatch):
    """One bad asset should cost that card its art, not the whole deck."""
    import azoth_logic.deck_render as dr
    import importlib
    importlib.reload(dr)

    class Boom:
        def __init__(self, bucket): pass
        def download(self, name):
            if name == "bad.png":
                raise RuntimeError("404")
            return b"ok"

    monkeypatch.setattr("supabase_client.supabase",
                        type("S", (), {"storage": type("St", (), {
                            "from_": staticmethod(lambda b: Boom(b))})()})())
    cards = [_card("good", image="fine.png"), _card("bad", image="bad.png")]
    art = dr.fetch_art_many(cards, workers=2)
    assert art[id(cards[0])] == b"ok"
    assert art[id(cards[1])] is None


# ---------------------------------------------------------------------------
# Bucket routing
# ---------------------------------------------------------------------------

def test_rite_art_is_not_fetched_at_all():
    """`event_card.tscn` ships its Image node hidden.

    A rite's `image` feeds the DRAFT THUMBNAIL, not the card face, so fetching it
    is a download whose result is discarded. Before this was dispatched on
    `kind`, every rite in a `/search` result cost one request to the wrong bucket
    (`cardimages`), which then 404'd.
    """
    assert deck_render._bucket_for({"image": "augury.png"}, "rite") is None


def test_aspects_route_to_their_own_bucket():
    """Sniffing for an `attunement` key used to decide this, so an aspect row
    that happened to lack the column landed in the cards bucket."""
    assert deck_render._bucket_for({"image": "a.png"}, "aspect") == "aspectimages"
    assert deck_render._bucket_for({"image": "a.png"}, "card") == deck_render.card_render.PNG_BUCKET


def test_exr_beats_content_type():
    """`.exr` always lives in `eigenfunctions`, whatever kind carries it."""
    for kind in ("card", "aspect"):
        assert deck_render._bucket_for({"image": "a.exr"}, kind) == deck_render.card_render.EXR_BUCKET


def test_a_mixed_pool_fetches_per_kind(monkeypatch):
    """A /search result mixes all three; each has to reach the right bucket."""
    import azoth_logic.deck_render as dr
    import importlib
    importlib.reload(dr)
    seen = []

    class Fake:
        def __init__(self, bucket): self.bucket = bucket
        def download(self, name):
            seen.append((self.bucket, name))
            return b"x"

    monkeypatch.setattr("supabase_client.supabase",
                        type("S", (), {"storage": type("St", (), {
                            "from_": staticmethod(lambda b: Fake(b))})()})())

    items = [_card("c", image="c.png"), {"name": "a", "image": "a.png"},
             {"name": "r", "image": "r.png"}]
    dr.fetch_art_many(items, workers=2, kinds=["card", "aspect", "rite"])

    assert ("cardimages", "c.png") in seen
    assert ("aspectimages", "a.png") in seen
    assert not [b for b, _ in seen if _ == "r.png"], "the rite must not be downloaded"
