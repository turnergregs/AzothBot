"""Solved art (azoth_logic/solved_art.py) against the game.

The fixture (tests/fixtures/solved_art_reference.json) is written by the azoth
repo's tools/SolvedArtReference.tscn: for a few looks, the game's solved set
(ModeSet), what ArtVisuals binds (frame, peak, partners) and the field and
zones the shader draws. A drifted solve would number the patterns differently
or draw them elsewhere, so the bot would show a card's art wrong; regenerate the
fixture when the game's solver changes (its solver_version)."""
import json
from pathlib import Path

import numpy as np
import pytest

from azoth_logic import eigenfunction_art, procedural_art, solved_art

FIXTURE = Path(__file__).parent / "fixtures" / "solved_art_reference.json"
REFERENCE = json.loads(FIXTURE.read_text())
CASES = REFERENCE["cases"]


def test_the_fixture_is_from_this_solver_version():
    assert REFERENCE["solver_version"] == solved_art.SOLVER_VERSION


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_solve_matches_the_games(case):
    solved = solved_art.mode_set(solved_art.resolve_solved(case["look"]))
    expected = case["set"]
    assert solved["mirrored"] == expected["mirrored"]
    assert solved["parities"] == expected["parities"]
    np.testing.assert_allclose(solved["values"], expected["values"], rtol=1e-6)
    for mine, theirs in zip(solved["modes"], expected["modes"]):
        # Half precision both sides, made the same way: equal but where the two
        # solvers' last float32 bits fall either side of a half step.
        np.testing.assert_allclose(mine, np.array(theirs, dtype=np.float32), atol=1e-3)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_field_matches_the_games(case):
    field = solved_art.SolvedField(case["look"], REFERENCE["size"], case["departure"], REFERENCE["threshold"])
    assert field.partners == case["partners"]
    np.testing.assert_allclose(field.frame, case["frame"], atol=1e-6)
    assert field.peak == pytest.approx(case["peak"], rel=1e-5)
    step = REFERENCE["step"]
    z = field.at(case["t"])[::step, ::step] / REFERENCE["threshold"]
    expected = np.array(case["field"])
    np.testing.assert_allclose(z, expected, atol=0.05)
    zones = field.zone(field.at(case["t"]))[::step, ::step] > 0.75
    clear = np.abs(expected) > 0.05
    assert np.array_equal(zones[clear], (np.array(case["zones"]) == 1)[clear])


def test_solved_art_is_art_and_draws_through_the_same_door():
    look = CASES[0]["look"]
    item = {"image_data": {"art": look, "departure": 0.1}}
    assert procedural_art.has_art(item)
    field = eigenfunction_art.procedural_field(item, size=32)
    assert isinstance(field, solved_art.SolvedField)
    assert field.at(0.0).shape == (32, 32)


def test_a_wave_look_still_draws_as_waves():
    item = {"image_data": {"art": {"version": 1, "family": "circle"}}}
    assert isinstance(eigenfunction_art.procedural_field(item, size=16), procedural_art.Field)
