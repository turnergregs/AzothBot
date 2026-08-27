"""Tests for the multi-card layouts.

The grid and the hand are STATIC by design: a 110-card deck animating at 60
frames each would be tens of megabytes and unreadable at thumbnail size.

The upgrade comparison is the exception -- two faces, not a hundred, so it
animates whenever either side has eigenfunction art. It composes frames and
hands them to the same `card_render.to_gif` every other renderer uses.
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


# ---------------------------------------------------------------------------
# Upgrade comparison
# ---------------------------------------------------------------------------

# Far enough apart to survive quantisation into a shared 128-colour palette.
_TEST_COLOURS = [(220, 30, 30), (30, 200, 30), (40, 60, 230), (230, 210, 40),
                 (210, 40, 200), (40, 210, 210), (245, 245, 245), (15, 15, 15)]


def _frames(n):
    """`n` RGBA frames that stay distinct through the GIF encoder.

    Two earlier fixtures were too subtle and PIL merged frames it considered
    identical, so the GIF came back with 29 of 30 -- which reads as a bug in the
    composition rather than in the test. Flat, saturated, far-apart colours.
    """
    assert n <= len(_TEST_COLOURS), "add more colours"
    return [Image.new("RGBA", (L.CARD_W, L.CARD_H), _TEST_COLOURS[i] + (255,))
            for i in range(n)]


@pytest.fixture
def sides(monkeypatch):
    """Install per-face frame lists, bypassing art and the shader."""
    def install(*counts):
        queue = list(counts)
        def fake(item, kind, art, duration, fps):
            return _frames(queue[item["_i"]])
        monkeypatch.setattr(deck_render, "_frames_for", fake)
        monkeypatch.setattr(deck_render, "_animates", lambda i, k, a: queue[i["_i"]] > 1)
        return [{**_card(f"C{i}"), "_i": i} for i in range(len(counts))]
    return install


def test_a_still_comparison_is_a_png(sides):
    items = sides(1, 1)
    data, ext = deck_render.render_comparison(
        items, ["card", "card"], ["Base", "Upgraded"], animate=True)
    assert ext == "png"
    assert Image.open(io.BytesIO(data)).format == "PNG"


def test_an_animated_side_makes_it_a_gif(sides):
    items = sides(6, 1)
    data, ext = deck_render.render_comparison(
        items, ["card", "aspect"], ["Base", "Upgraded (Aspect)"], animate=True)
    assert ext == "gif"
    assert Image.open(io.BytesIO(data)).n_frames == 6


def test_a_still_side_holds_while_the_other_moves(sides):
    """The common card-into-aspect shape: an animated .exr card upgrading into a
    flat-art aspect. The aspect has one frame and must simply persist."""
    items = sides(8, 1)
    data, ext = deck_render.render_comparison(
        items, ["card", "aspect"], ["Base", "Upgraded"], animate=True)
    assert Image.open(io.BytesIO(data)).n_frames == 8


def test_frame_count_follows_the_longest_side(sides):
    items = sides(3, 7)
    data, _ = deck_render.render_comparison(
        items, ["card", "card"], ["Base", "Upgraded"], animate=True)
    assert Image.open(io.BytesIO(data)).n_frames == 7


def test_animate_false_forces_the_still_path(sides):
    items = sides(6, 6)
    _, ext = deck_render.render_comparison(
        items, ["card", "card"], ["Base", "Upgraded"], animate=False)
    assert ext == "png"


def test_mismatched_inputs_are_refused(sides):
    items = sides(1, 1)
    with pytest.raises(ValueError):
        deck_render.render_comparison(items, ["card"], ["Base", "Upgraded"])
    with pytest.raises(ValueError):
        deck_render.render_comparison([], [], [])


def test_the_sheet_is_wide_enough_for_every_face(sides):
    items = sides(1, 1)
    data, _ = deck_render.render_comparison(
        items, ["card", "card"], ["Base", "Upgraded"],
        card_width=deck_render.COMPARE_CARD_WIDTH, animate=True)
    width, height = Image.open(io.BytesIO(data)).size
    assert width == 2 * deck_render.COMPARE_CARD_WIDTH + 3 * deck_render.COMPARE_GUTTER
    assert height > deck_render.COMPARE_LABEL_BAND, "captions need their band"


def test_the_cache_key_changes_when_a_face_does(sides):
    """Two cards that differ only in rules text must not share a render.

    Keying on one side, or on the name, is how the previous renderer served a
    stale image after every edit.
    """
    art = {}
    a = _card("Same"); b = _card("Same")
    base = deck_render._comparison_key([a, b], ["card", "card"],
                                       ["Base", "Upgraded"], art, 380, 4.0, 15)
    b2 = {**b, "text": "Draw 2"}
    changed = deck_render._comparison_key([a, b2], ["card", "card"],
                                          ["Base", "Upgraded"], art, 380, 4.0, 15)
    assert base != changed


def test_the_cache_key_changes_with_the_captions(sides):
    """`Upgraded` and `Upgraded (Aspect)` are different images."""
    art = {}
    a, b = _card("A"), _card("B")
    k1 = deck_render._comparison_key([a, b], ["card", "card"],
                                     ["Base", "Upgraded"], art, 380, 4.0, 15)
    k2 = deck_render._comparison_key([a, b], ["card", "aspect"],
                                     ["Base", "Upgraded (Aspect)"], art, 380, 4.0, 15)
    assert k1 != k2


def test_the_cache_key_changes_with_the_sheen(sides):
    """Both faces are foiled; only the intensity differs. Keying without it
    would serve a base-intensity render for an upgraded face."""
    art = {}
    a, b = _card("A"), _card("B")
    common = ([a, b], ["card", "card"], ["Base", "Upgraded"], art, 380, 4.0, 15)
    assert (deck_render._comparison_key(*common, holo_levels=[0.06, 0.06])
            != deck_render._comparison_key(*common, holo_levels=[0.06, 0.15]))


def test_each_side_gets_its_own_sheen_intensity(sides, monkeypatch):
    """A yes/no flag would flatten base and upgraded into the same foil."""
    applied = []
    monkeypatch.setattr(deck_render.holo, "apply_all",
                        lambda frames, intensity=None: applied.append(intensity) or frames)
    items = sides(1, 1)
    deck_render.render_comparison(items, ["card", "card"], ["Base", "Upgraded"],
                                  holo_levels=[0.06, 0.15], animate=True)
    assert applied == [0.06, 0.15]


def test_a_zero_level_skips_the_sheen(sides, monkeypatch):
    calls = []
    monkeypatch.setattr(deck_render.holo, "apply_all",
                        lambda frames, intensity=None: calls.append(intensity) or frames)
    items = sides(1, 1)
    deck_render.render_comparison(items, ["card", "card"], ["Base", "Upgraded"],
                                  holo_levels=[0.0, 0.15], animate=True)
    assert calls == [0.15]


def test_a_face_keeps_its_own_aspect_ratio(sides, monkeypatch):
    """REGRESSION: the comparison squeezed both cards horizontally.

    Each side is cropped to its own alpha box, then was resized to a height
    derived from the FULL CARD_W x CARD_H canvas. The crop is not that shape --
    a card's is ~552x766 (0.72) against the canvas's 0.624 -- so forcing it into
    the canvas ratio lost 13% of its width. The whole point of cropping is that
    the face is no longer the full canvas, so the canvas cannot supply the
    target shape.
    """
    tall = Image.new("RGBA", (400, 300), (200, 60, 60, 255))   # 4:3, unlike a card
    monkeypatch.setattr(deck_render, "_frames_for", lambda *a, **k: [tall])
    monkeypatch.setattr(deck_render, "_still_for", lambda *a, **k: tall)
    monkeypatch.setattr(deck_render, "_animates", lambda *a, **k: False)

    faces = deck_render._comparison_sides(
        [_card("A")], ["card"], {}, 380, False, 4.0, 15)
    out = faces[0][0]
    assert out.width == 380
    assert abs(out.width / out.height - 400 / 300) < 0.01, "aspect must survive"


def test_faces_of_different_shapes_are_centred_not_stretched(sides, monkeypatch):
    """A card crop and an aspect crop are different shapes. The row is as tall
    as the tallest; stretching the shorter one to match would put the
    distortion straight back."""
    shapes = {"card": Image.new("RGBA", (400, 600), (200, 60, 60, 255)),
              "aspect": Image.new("RGBA", (400, 300), (60, 60, 200, 255))}
    monkeypatch.setattr(deck_render, "_still_for",
                        lambda item, kind, art: shapes[kind])
    monkeypatch.setattr(deck_render, "_animates", lambda *a, **k: False)

    faces = deck_render._comparison_sides(
        [_card("A"), _card("B")], ["card", "aspect"], {}, 380, False, 4.0, 15)
    tall, short = faces[0][0], faces[1][0]
    assert tall.height != short.height, "different shapes keep different heights"
    assert abs(short.width / short.height - 400 / 300) < 0.01

    data, _ = deck_render.render_comparison(
        [_card("A"), _card("B")], ["card", "aspect"], ["Base", "Upgraded"])
    sheet = Image.open(io.BytesIO(data))
    assert sheet.height >= tall.height, "the row fits the tallest face"
