"""Tests for `/show`'s detail view, and `/rules`' mechanics file.

`/show` used to dump the raw database row as JSON, which buried the two things
you actually want -- the rules text and the card's stats -- under audit metadata,
rendering internals and a nested `upgrades` blob.

`/rules` is where those omitted blobs went: the four `jsonb` fields, as a file
attachment, because each one runs past Discord's 2000-character message limit on
its own. The documented way to read them was previously "query the database
directly", for the fields that actually define the mechanic.
"""
import pytest

from azoth_commands.content import (MECHANIC_FIELDS, _accent, _comparison_labels,
                                    _facts, _mechanics)
from azoth_logic import card_layout, fate_layout

CARD = {"id": 193, "name": "Ablution", "type": "spell", "element": "sol", "valence": 2,
        "subtypes": ["Sacred"], "text": "Draw 1", "split": None,
        # everything below must stay out of the output
        "created_by": 11, "created_at": "2025-05-21", "updated_at": "2026-08-25",
        "image": "x.exr", "image_data": {}, "attributes": [],
        "upgrades": [{"text": "Draw 2", "actions": [{"name": "Draw", "amount": 2}]}]}


def _labels(kind, row):
    return [l for l, _ in _facts(kind, row)]


def _value(kind, row, label):
    return dict(_facts(kind, row))[label]


# ---------------------------------------------------------------------------
# What is shown
# ---------------------------------------------------------------------------

def test_card_shows_its_defining_attributes():
    assert _labels("card", CARD) == ["Element", "Valence", "Subtypes"]
    assert _value("card", CARD, "Element") == "Sol"
    assert _value("card", CARD, "Valence") == "2"
    assert _value("card", CARD, "Subtypes") == "Sacred"


def test_aspect_shows_attunement():
    assert _labels("aspect", {"attunement": 2}) == ["Attunement"]


def test_rite_shows_foresight():
    assert _labels("rite", {"foresight": 3}) == ["Foresight"]


def test_split_card_shows_its_second_face():
    row = {**CARD, "split": {"element": "sol", "valence": 4}}
    assert _value("card", row, "Split") == "Sol valence 4"


def test_colourless_is_named_not_left_blank():
    """64 of 400 cards have no element. `null` told you nothing."""
    assert _value("card", {**CARD, "element": None}, "Element") == "Colourless"


def test_catalyst_type_is_shown_but_spell_is_not():
    """`spell` is 328 of 400 cards -- showing it on every one is noise. A
    catalyst is the exception worth calling out."""
    assert "Type" not in _labels("card", CARD)
    assert _value("card", {**CARD, "type": "catalyst"}, "Type") == "Catalyst"


# ---------------------------------------------------------------------------
# What is NOT shown -- the point of the change
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("noise", [
    "upgrades",                       # a nested blob that dwarfs the card
    "created_at", "updated_at", "created_by",   # audit metadata
    "image", "image_data",            # rendering internals; /render is the view
])
def test_noise_fields_never_appear(noise):
    rendered = " ".join(f"{l}{v}" for l, v in _facts("card", CARD)).lower()
    assert noise.replace("_", "") not in rendered.replace(" ", "").replace("_", "")


def test_upgrade_text_does_not_leak_into_the_view():
    """The upgrade's own rules text is the thing most likely to be mistaken for
    the card's."""
    rendered = " ".join(str(v) for _, v in _facts("card", CARD))
    assert "Draw 2" not in rendered


def test_empty_and_null_values_are_dropped():
    """Showing `subtypes: []` and `split: null` is most of what made the raw
    dump unreadable."""
    bare = {"id": 1, "name": "X", "type": "spell", "element": "sol",
            "valence": None, "subtypes": [], "split": None, "attributes": []}
    assert _labels("card", bare) == ["Element"]


def test_zero_valence_is_shown_not_treated_as_absent():
    assert _value("card", {**CARD, "valence": 0}, "Valence") == "0"


def test_zero_attunement_is_shown():
    assert _labels("aspect", {"attunement": 0}) == ["Attunement"]


# ---------------------------------------------------------------------------
# Accent colour
# ---------------------------------------------------------------------------

def test_card_accent_is_its_element():
    r, g, b = card_layout.element_color("sol")
    assert _accent("card", CARD) == (r << 16) | (g << 8) | b


def test_aspect_accent_is_its_own_palette():
    aspect = {"image_data": {"primary_color": [246, 83, 83],
                             "secondary_color": [9, 242, 210]}}
    assert _accent("aspect", aspect) == (9 << 16) | (242 << 8) | 210


def test_rite_accent_falls_back_when_it_has_no_palette():
    r, g, b = fate_layout.RITE_NAME_COLOR
    assert _accent("rite", {"image_data": {}}) == (r << 16) | (g << 8) | b


def test_accent_is_a_valid_discord_colour():
    for kind, row in [("card", CARD), ("aspect", {}), ("rite", {})]:
        assert 0 <= _accent(kind, row) <= 0xFFFFFF


# ---------------------------------------------------------------------------
# /rules -- the mechanics file
# ---------------------------------------------------------------------------

def test_the_four_jsonb_fields_are_exactly_what_show_omits():
    """The two commands are complements. If `/show` starts printing one of these
    or `/rules` stops carrying it, they have drifted apart."""
    assert MECHANIC_FIELDS == ("actions", "triggers", "properties", "upgrades")
    for field in MECHANIC_FIELDS:
        assert field not in _labels("card", CARD)


def test_populated_fields_are_carried():
    row = {"actions": [{"name": "Draw"}], "triggers": [], "properties": [],
           "upgrades": [{"text": "Draw 2"}]}
    assert set(_mechanics(row)) == {"actions", "upgrades"}


def test_empty_fields_are_dropped_not_emitted_as_empty_arrays():
    """`{"triggers": []}` reads as "this has no triggers", which is a different
    claim from "this card does not use triggers"."""
    row = {"actions": [{"name": "Draw"}], "triggers": [], "properties": None}
    assert _mechanics(row) == {"actions": [{"name": "Draw"}]}


def test_a_card_with_no_mechanics_yields_nothing():
    """27 cards are in this state -- their rules text is all there is. The
    command says so rather than sending an empty file."""
    assert _mechanics({"name": "Burnout", "text": "Draw 1", "actions": []}) == {}


def test_upgrades_are_included():
    """`/show` omits `upgrades` because it dwarfs the card. That is exactly why
    it belongs here -- and since a card can upgrade INTO an aspect, the upgrade
    payload is often the only place that transformation is visible."""
    assert "upgrades" in _mechanics(CARD)


def test_field_order_is_stable():
    """Declaration order, not dict-insertion order from the database row, so two
    cards' files diff against each other."""
    row = {"upgrades": [1], "properties": [2], "triggers": [3], "actions": [4]}
    assert list(_mechanics(row)) == ["actions", "triggers", "properties", "upgrades"]


# ---------------------------------------------------------------------------
# /render -- upgrade comparison captions
# ---------------------------------------------------------------------------

def test_a_single_tier_is_just_base_and_upgraded():
    """Every card in the database has exactly one tier, so this is the caption
    almost everyone sees. `Tier 1` would be noise."""
    assert _comparison_labels([({}, "card", 1)]) == ["Base", "Upgraded"]


def test_a_type_change_is_named():
    """28 cards upgrade into aspects. Without the caption the second face just
    looks like a different card."""
    assert _comparison_labels([({}, "aspect", 1)]) == ["Base", "Upgraded (Aspect)"]


def test_staying_a_card_is_not_worth_saying():
    assert "(Card)" not in " ".join(_comparison_labels([({}, "card", 1)]))


def test_multiple_tiers_are_numbered():
    labels = _comparison_labels([({}, "card", 1), ({}, "aspect", 2)])
    assert labels == ["Base", "Tier 1", "Tier 2 (Aspect)"]


def test_labels_stay_ascii():
    """The card font has no arrow glyph, and a missing glyph renders as a silent
    gap -- `Upgraded -> Aspect` came out as `Upgraded   Aspect`."""
    for label in _comparison_labels([({}, "aspect", 1)]):
        assert label.isascii(), label
