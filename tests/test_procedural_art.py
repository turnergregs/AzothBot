"""Procedural art (`image_data.art`, made in the game's Codex).

Two things are pinned here. The port itself, against values the GAME rendered on
a GPU from the real shader (tests/fixtures/procedural_art_reference.json, made by
tools/procedural_art_reference.gd in the azoth repo): a drifted formula would
otherwise render plausible art that is simply not the card's. And the routing:
art wins over `image` as in the game, and is computed, never downloaded, so a
card updated with new art shows it rather than its old `.exr`.

Regenerate the fixture after changing either side's maths; see the tool's header.
"""
import io
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from azoth_logic import art_cache, card_render, deck_render, fate_render
from azoth_logic import eigenfunction_art as ef
from azoth_logic import procedural_art as pa

FIXTURE = Path(__file__).parent / "fixtures" / "procedural_art_reference.json"
REFERENCE = json.loads(FIXTURE.read_text())

# In threshold units, where art peaks at 5-8. The fixture's packing resolves
# about 0.002; the port measured within 0.003 everywhere, the edge fade included.
FIELD_TOLERANCE = 0.01

ART = {"version": 1, "family": "circle", "modes": [[3, 4], [0, 2]],
       "params": [[1.0, 0.25], [0.3, 0.0]]}


def _card(**extra):
    card = {"name": "Seethe", "element": "blood", "valence": 3, "text": "Draw 1.",
            "subtypes": [], "split": None, "image": "old_art__BALL_ef13_ef.exr",
            "image_data": {"departure": 0.05, "art": ART}}
    card.update(extra)
    return card


@pytest.fixture
def no_downloads(monkeypatch):
    def refuse(*_):
        raise AssertionError("procedural art must not download anything")
    monkeypatch.setattr(card_render, "download_art", refuse)


# ---------------------------------------------------------------------------
# Parity with the game's shader
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", REFERENCE["cases"],
                         ids=lambda c: f"{c['name']}@t{c['t']}")
def test_field_matches_the_gpu(case):
    field = pa.Field(case["art"], REFERENCE["size"], case["departure"], REFERENCE["threshold"])
    z = field.at(case["t"])
    samples = np.array(case["samples"])
    xs, ys = samples[:, 0].astype(int), samples[:, 1].astype(int)
    mine = z[ys, xs] / REFERENCE["threshold"]
    worst = float(np.max(np.abs(mine - samples[:, 2])))
    assert worst < FIELD_TOLERANCE, f"{case['name']} off by {worst:.4f} threshold units"


@pytest.mark.parametrize("case", REFERENCE["cases"],
                         ids=lambda c: f"{c['name']}@t{c['t']}")
def test_zones_match_the_gpu(case):
    """Which colour each blob takes. Compared where the field is clearly off
    zero: a sign rule on a value the packing rounds to 0 is a coin toss."""
    field = pa.Field(case["art"], REFERENCE["size"], case["departure"], REFERENCE["threshold"])
    z = field.at(case["t"])
    zone = field.zone(z) > 0.75
    samples = np.array(case["samples"])
    xs, ys = samples[:, 0].astype(int), samples[:, 1].astype(int)
    clear = np.abs(samples[:, 2]) > 0.05
    assert np.array_equal(zone[ys, xs][clear], samples[:, 3][clear].astype(bool))


def test_the_fixture_covers_every_family_and_framing():
    arts = [c["art"] for c in REFERENCE["cases"]]
    assert {a["family"] for a in arts} == set(pa.FAMILIES)
    assert {a.get("framing", "whole") for a in arts} == {"whole", "cropped"}
    assert any(a.get("warp") for a in arts)
    assert {c["t"] for c in REFERENCE["cases"]} >= {0.0}
    assert any(c["departure"] > 0 for c in REFERENCE["cases"]), "the wobble is covered"


# ---------------------------------------------------------------------------
# Reading a look
# ---------------------------------------------------------------------------

def test_an_unreadable_version_is_no_art():
    """The game draws nothing for a version it can't read rather than guess."""
    assert not pa.has_art({"image_data": {"art": dict(ART, version=2)}})
    assert not pa.has_art({"image_data": {"art": {}}})
    assert not pa.has_art({"image_data": None})
    assert pa.has_art({"image_data": {"art": ART}})


def test_resolve_takes_the_family_framing():
    look = pa.resolve({"family": "square"})
    assert look["scale"] == 0.85 and look["center"] == [0.0, 0.0]
    assert pa.resolve({"family": "nonsense"})["family"] == "triangle"


# ---------------------------------------------------------------------------
# Routing: art wins over image, and is never downloaded
# ---------------------------------------------------------------------------

def test_art_wins_over_the_exr_it_keeps_for_old_clients(no_downloads):
    card = _card()
    assert card_render.is_animated(card)
    assert not ef.needs_download(card)
    assert card_render.fetch_art(card) is None


def test_an_exr_card_still_downloads():
    card = _card(image_data={"departure": 0.05})
    assert ef.needs_download(card)
    assert card_render.art_bucket(card) == card_render.EXR_BUCKET


def test_a_card_with_art_renders_its_art(no_downloads):
    still = card_render.render_still(_card(), None)
    plain = card_render.render_still(_card(image_data={}, image=None), None)
    assert np.asarray(still).tobytes() != np.asarray(plain).tobytes()


def test_a_card_with_art_animates_with_no_bytes(no_downloads):
    data = card_render.render_gif(_card(), None, duration=0.4, fps=5)
    gif = Image.open(io.BytesIO(data))
    assert gif.n_frames == 2


def test_render_with_art_returns_a_gif(no_downloads, tmp_path, monkeypatch):
    monkeypatch.setattr(art_cache, "RENDER_DIR", tmp_path)
    data, ext = card_render.render(_card(), duration=0.4, fps=5)
    assert ext == "gif" and data


def test_new_art_misses_the_render_cache():
    """A bulk_update that changes only the art must not serve the old render."""
    other = dict(ART, modes=[[5, 2]])
    before = art_cache.render_key(_card(), None, "card")
    after = art_cache.render_key(_card(image_data={"departure": 0.05, "art": other}), None, "card")
    assert before != after


def test_an_aspect_with_art_renders_without_downloading(no_downloads):
    aspect = {"name": "Readiness", "text": "+1", "image": "a.exr",
              "image_data": {"primary_color": [246, 83, 83], "secondary_color": [9, 242, 210],
                             "art": ART}}
    assert fate_render.fetch_art(aspect, "aspectimages") is None
    data, ext = fate_render.render_aspect(aspect, None, duration=0.4, fps=5)
    assert ext == "gif"


def test_the_deck_grid_skips_the_download(no_downloads):
    card = _card()
    assert deck_render.fetch_art_many([card]) == {id(card): None}
    assert deck_render._animates(card, "card", None)
